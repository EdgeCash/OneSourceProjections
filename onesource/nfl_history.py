"""Parse sportsoddshistory-style NFL weekly results (with closing spread and
total) into our committed history stores:

  - data/history/backfill/nfl/<season>/games.json.gz   (results, lines)
  - data/history/closing_lines/nfl/<season>.jsonl.gz   (spread + total @ -110)

The pasted format is tab-delimited per game, grouped under
"<YYYY> Regular Season - Week <N>" and "<YYYY> Playoffs" headers, e.g.

  Sun  Sep 11, 2016  1:04  @  Baltimore Ravens  W 13-7  W -3    Buffalo Bills  U 44.5
  Green Bay Packers  W 27-23  W -3.5  @  Jacksonville Jaguars  O 47

The team listed first is the favorite and carries the score (its points
first) and the spread (negative); "@" marks the home side; "N" marks a
neutral site; playoff rows have a round label first and "(seed)" suffixes.
Moneyline prices aren't in this source — spread and total only.
"""

from __future__ import annotations

import gzip
import json
import re
from datetime import datetime
from pathlib import Path

_DOW = {"Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"}


def _american_to_decimal(american: int) -> float:
    return round(1 + (100 / abs(american) if american < 0 else american / 100), 4)


_ML = -110
_DEC = _american_to_decimal(_ML)


def _strip_seed(name: str) -> str:
    return re.sub(r"\s*\(\d+\)\s*$", "", name).strip()


def _nick(name: str) -> str:
    return (name.split()[-1] if name else "").lower()


def _parse_score(tok: str):
    s = tok.strip()
    m = re.match(r"^([WLT])\s+(\d+)\s*-\s*(\d+)", s)
    if not m:
        return None
    return {"letter": m.group(1), "a": int(m.group(2)), "b": int(m.group(3)),
            "ot": "OT" in s}


def _parse_spread(tok: str):
    m = re.match(r"^([WLP])\s+(PK|[-+]?\d+(?:\.\d+)?)$", tok.strip())
    if not m:
        return None
    return 0.0 if m.group(2) == "PK" else float(m.group(2))


def _parse_total(tok: str):
    m = re.match(r"^([OUP])\s+(\d+(?:\.\d+)?)$", tok.strip())
    return float(m.group(2)) if m else None


def parse_games(text: str) -> list[dict]:
    """Parse the full dump into one record per game (results + closing lines)."""
    games: list[dict] = []
    season = week = None
    season_type = 2
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        head = line.strip()
        m = re.match(r"^(\d{4}) Regular Season - Week (\d+)", head)
        if m:
            season, week, season_type = int(m.group(1)), int(m.group(2)), 2
            continue
        m = re.match(r"^(\d{4}) Playoffs", head)
        if m:
            season, week, season_type = int(m.group(1)), 19, 3
            continue
        if season is None:
            continue
        g = _parse_line(line, season, week, season_type)
        if g:
            games.append(g)
    return games


def _parse_line(line: str, season: int, week: int, season_type: int):
    toks = [t.strip() for t in line.split("\t") if t.strip()]
    if len(toks) < 7:
        return None
    i = 0
    rnd = None
    if toks[0] not in _DOW:
        if len(toks) > 1 and toks[1] in _DOW:      # playoff round label
            rnd, i = toks[0], 1
        else:
            return None
    if toks[i] not in _DOW:
        return None
    i += 1                                          # day of week
    try:
        dt = datetime.strptime(toks[i], "%b %d, %Y")
    except (ValueError, IndexError):
        return None
    i += 1                                          # date
    if i < len(toks) and re.match(r"^\d{1,2}:\d{2}$", toks[i]):
        i += 1                                      # time (optional)

    team1_home = neutral = False
    if i < len(toks) and toks[i] == "@":
        team1_home, i = True, i + 1
    elif i < len(toks) and toks[i] == "N":
        neutral, i = True, i + 1
    if i >= len(toks):
        return None
    team1 = _strip_seed(toks[i]); i += 1
    if i >= len(toks):
        return None
    score = _parse_score(toks[i]); i += 1
    if not score or i >= len(toks):
        return None
    spread = _parse_spread(toks[i]); i += 1
    if spread is None:
        return None

    team2_home = False
    if i < len(toks) and toks[i] == "@":
        team2_home, i = True, i + 1
    elif i < len(toks) and toks[i] == "N":
        neutral, i = True, i + 1
    if i >= len(toks):
        return None
    team2 = _strip_seed(toks[i]); i += 1
    total = _parse_total(toks[i]) if i < len(toks) else None

    # team1 is the favorite and carries its own points first
    if team1_home:
        home, away, hs, as_ = team1, team2, score["a"], score["b"]
        home_line = spread
    elif team2_home:
        home, away, hs, as_ = team2, team1, score["b"], score["a"]
        home_line = -spread
    else:                                            # neutral: team1 = away
        home, away, hs, as_ = team2, team1, score["b"], score["a"]
        home_line = -spread
        neutral = True

    winner = "" if hs == as_ else (home if hs > as_ else away)
    return {
        "season": season, "week": week, "season_type": season_type,
        "playoff_round": rnd,
        "game_id": f"{season}_{dt.date().isoformat()}_{_nick(away)}_{_nick(home)}",
        "date": dt.date().isoformat(),
        "home_team": home, "away_team": away,
        "home_score": hs, "away_score": as_,
        "total_points": hs + as_, "margin": hs - as_,
        "ml_winner": winner, "completed": True,
        "neutral_site": neutral, "is_ot": score["ot"],
        "spread_home": home_line, "over_under": total,
    }


def closing_line_rows(g: dict) -> list[dict]:
    """Spread (home/away) and total (over/under) rows at -110 for one game,
    in the schema history.closing_lines / backtest.closing_consensus expect."""
    base = {
        "event_id": g["game_id"], "book": "close",
        "captured_at": f"{g['date']}T12:00:00Z",
        "scheduled_start": f"{g['date']}T12:00:00Z",
        "home_team": g["home_team"], "away_team": g["away_team"],
        "american_odds": _ML, "decimal_odds": _DEC,
        "season": g["season"], "sport": "NFL", "sport_key": "NFL",
    }
    rows = []
    if g.get("spread_home") is not None:
        rows.append({**base, "market": "spread", "side": "home",
                     "line": g["spread_home"]})
        rows.append({**base, "market": "spread", "side": "away",
                     "line": -g["spread_home"]})
    if g.get("over_under") is not None:
        rows.append({**base, "market": "total", "side": "over",
                     "line": g["over_under"]})
        rows.append({**base, "market": "total", "side": "under",
                     "line": g["over_under"]})
    return rows


def write_history(games: list[dict], root: Path, overwrite_games: bool = False) -> dict:
    """Write per-season games + closing_lines stores. Existing games.json.gz
    is preserved unless overwrite_games (so the ESPN 2025 backfill with player
    logs stays). Returns {season: {games, lines}} counts."""
    by_season: dict[int, list[dict]] = {}
    for g in games:
        by_season.setdefault(g["season"], []).append(g)

    counts: dict[int, dict] = {}
    for season, gs in sorted(by_season.items()):
        gdir = root / "backfill" / "nfl" / str(season)
        gdir.mkdir(parents=True, exist_ok=True)
        gpath = gdir / "games.json.gz"
        if overwrite_games or not gpath.exists():
            with gzip.open(gpath, "wt") as f:
                json.dump(gs, f)

        cdir = root / "closing_lines" / "nfl"
        cdir.mkdir(parents=True, exist_ok=True)
        rows = [r for g in gs for r in closing_line_rows(g)]
        with gzip.open(cdir / f"{season}.jsonl.gz", "wt") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        counts[season] = {"games": len(gs), "lines": len(rows)}
    return counts


def main(argv: list[str] | None = None) -> None:
    import argparse

    from . import config

    ap = argparse.ArgumentParser(description="Ingest sportsoddshistory NFL dump")
    ap.add_argument("input", help="raw pasted text file")
    ap.add_argument("--root", default=str(config.REPO_ROOT / "data" / "history"))
    ap.add_argument("--overwrite-games", action="store_true")
    args = ap.parse_args(argv)

    text = Path(args.input).read_text()
    games = parse_games(text)
    counts = write_history(games, Path(args.root), args.overwrite_games)
    total_g = sum(c["games"] for c in counts.values())
    total_l = sum(c["lines"] for c in counts.values())
    print(f"parsed {total_g} games across {len(counts)} seasons "
          f"({total_l} closing-line rows)")
    for season, c in counts.items():
        print(f"  {season}: {c['games']} games, {c['lines']} lines")


if __name__ == "__main__":
    main()
