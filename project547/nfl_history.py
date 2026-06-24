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


def closing_line_rows(g: dict, sport: str = "NFL") -> list[dict]:
    """Closing-line rows for one game in the schema history.closing_lines /
    backtest.closing_consensus expect. Emits spread (home/away) + total
    (over/under) + moneyline (home/away) whenever each is present, using real
    American odds where given, else -110."""
    base = {
        "event_id": g["game_id"], "book": "close",
        "captured_at": f"{g['date']}T12:00:00Z",
        "scheduled_start": f"{g['date']}T12:00:00Z",
        "home_team": g["home_team"], "away_team": g["away_team"],
        "season": g["season"], "sport": sport, "sport_key": sport,
    }
    rows: list[dict] = []

    def add(market, side, line, american):
        am = int(american) if american is not None else _ML
        rows.append({**base, "market": market, "side": side, "line": line,
                     "american_odds": am, "decimal_odds": _american_to_decimal(am)})

    if g.get("spread_home") is not None:
        add("spread", "home", g["spread_home"], g.get("home_spread_odds"))
        add("spread", "away", -g["spread_home"], g.get("away_spread_odds"))
    if g.get("over_under") is not None:
        add("total", "over", g["over_under"], g.get("over_odds"))
        add("total", "under", g["over_under"], g.get("under_odds"))
    if g.get("home_ml") is not None and g.get("away_ml") is not None:
        add("moneyline", "home", None, g["home_ml"])
        add("moneyline", "away", None, g["away_ml"])
    return rows


def write_history(games: list[dict], root: Path, sport: str = "NFL",
                  overwrite_games: bool = False) -> dict:
    """Write per-season games + closing_lines stores for a sport. Existing
    games.json.gz is preserved unless overwrite_games (so e.g. the ESPN NFL
    2025 backfill with player logs stays). Returns {season: {games, lines}}."""
    sk = sport.lower()
    by_season: dict[int, list[dict]] = {}
    for g in games:
        by_season.setdefault(g["season"], []).append(g)

    counts: dict[int, dict] = {}
    for season, gs in sorted(by_season.items()):
        gdir = root / "backfill" / sk / str(season)
        gdir.mkdir(parents=True, exist_ok=True)
        gpath = gdir / "games.json.gz"
        if overwrite_games or not gpath.exists():
            with gzip.open(gpath, "wt") as f:
                json.dump(gs, f)

        cdir = root / "closing_lines" / sk
        cdir.mkdir(parents=True, exist_ok=True)
        rows = [r for g in gs for r in closing_line_rows(g, sport)]
        with gzip.open(cdir / f"{season}.jsonl.gz", "wt") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        counts[season] = {"games": len(gs), "lines": len(rows)}
    return counts


def games_from_legacy_multi(rows: list[dict]) -> list[dict]:
    """Convert the committed legacy history (data/history/backtest/legacy/
    history_<lg>_multi.jsonl.gz) — NBA/NHL games with closing moneyline +
    total — into our game records. Season label = the season's start year
    (Sep+ -> that year, else prior year). No spread in this source."""
    out = []
    for r in rows:
        if not r.get("graded") or r.get("actual_home") is None or r.get("actual_away") is None:
            continue
        d = str(r["date"])[:10]
        y, m = int(d[:4]), int(d[5:7])
        season = y if m >= 9 else y - 1
        home, away = r["home"], r["away"]
        hs, as_ = int(r["actual_home"]), int(r["actual_away"])
        out.append({
            "season": season, "week": None, "season_type": 2, "playoff_round": None,
            "game_id": f"{d}_{away}_{home}",
            "date": d, "home_team": home, "away_team": away,
            "home_score": hs, "away_score": as_,
            "total_points": hs + as_, "margin": hs - as_,
            "ml_winner": "" if hs == as_ else (home if hs > as_ else away),
            "completed": True, "neutral_site": False, "is_ot": False,
            "spread_home": _num(r.get("mkt_spread_home")),
            "over_under": _num(r.get("mkt_total")),
            "home_ml": _num(r.get("mkt_home_ml")), "away_ml": _num(r.get("mkt_away_ml")),
        })
    return out


def ingest_legacy_multi(jsonl_gz: str | Path, root: Path, sport: str) -> dict:
    """Read a legacy history_<lg>_multi.jsonl.gz and write the sport's backfill
    + closing-line stores (results + moneyline/total)."""
    rows = [json.loads(ln) for ln in gzip.open(jsonl_gz, "rt") if ln.strip()]
    return write_history(games_from_legacy_multi(rows), Path(root), sport)


# nflverse team abbreviation -> current franchise full name (relocated teams
# map to their current name across all eras so Elo keeps franchise continuity
# and 2025 lines join the ESPN backfill, which uses current names).
TEAM_FULL = {
    "ARI": "Arizona Cardinals", "ATL": "Atlanta Falcons", "BAL": "Baltimore Ravens",
    "BUF": "Buffalo Bills", "CAR": "Carolina Panthers", "CHI": "Chicago Bears",
    "CIN": "Cincinnati Bengals", "CLE": "Cleveland Browns", "DAL": "Dallas Cowboys",
    "DEN": "Denver Broncos", "DET": "Detroit Lions", "GB": "Green Bay Packers",
    "HOU": "Houston Texans", "IND": "Indianapolis Colts", "JAX": "Jacksonville Jaguars",
    "KC": "Kansas City Chiefs", "LV": "Las Vegas Raiders", "OAK": "Las Vegas Raiders",
    "LAC": "Los Angeles Chargers", "SD": "Los Angeles Chargers",
    "LA": "Los Angeles Rams", "LAR": "Los Angeles Rams", "STL": "Los Angeles Rams",
    "MIA": "Miami Dolphins", "MIN": "Minnesota Vikings", "NE": "New England Patriots",
    "NO": "New Orleans Saints", "NYG": "New York Giants", "NYJ": "New York Jets",
    "PHI": "Philadelphia Eagles", "PIT": "Pittsburgh Steelers",
    "SF": "San Francisco 49ers", "SEA": "Seattle Seahawks",
    "TB": "Tampa Bay Buccaneers", "TEN": "Tennessee Titans",
    "WAS": "Washington Commanders",
}


def _num(v):
    """NaN/empty -> None, else the value."""
    if v is None:
        return None
    if isinstance(v, float) and v != v:   # NaN
        return None
    return v


def games_from_nflverse(records: list[dict]) -> list[dict]:
    """Map nflverse games.csv rows -> our game records (results + closing
    spread/total/moneyline). spread_line is home-favored-by-X (positive), so
    the American home spread is its negation."""
    out = []
    for r in records:
        if _num(r.get("home_score")) is None or _num(r.get("away_score")) is None:
            continue                                   # unplayed
        season = int(r["season"])
        gt = str(r.get("game_type", "REG"))
        home = TEAM_FULL.get(r["home_team"], r["home_team"])
        away = TEAM_FULL.get(r["away_team"], r["away_team"])
        hs, as_ = int(r["home_score"]), int(r["away_score"])
        sl = _num(r.get("spread_line"))
        out.append({
            "season": season, "week": int(r["week"]),
            "season_type": 2 if gt == "REG" else 3,
            "playoff_round": None if gt == "REG" else gt,
            "game_id": str(r.get("game_id") or f"{season}_{r['week']}_{away}_{home}"),
            "date": str(r["gameday"])[:10],
            "home_team": home, "away_team": away,
            "home_score": hs, "away_score": as_,
            "total_points": hs + as_, "margin": hs - as_,
            "ml_winner": "" if hs == as_ else (home if hs > as_ else away),
            "completed": True,
            "neutral_site": str(_num(r.get("location"))) == "Neutral",
            "is_ot": _num(r.get("overtime")) in (1, 1.0),
            "home_qb": _num(r.get("home_qb_id")), "away_qb": _num(r.get("away_qb_id")),
            "spread_home": (-float(sl)) if sl is not None else None,
            "over_under": _num(r.get("total_line")),
            "home_ml": _num(r.get("home_moneyline")),
            "away_ml": _num(r.get("away_moneyline")),
            "home_spread_odds": _num(r.get("home_spread_odds")),
            "away_spread_odds": _num(r.get("away_spread_odds")),
            "over_odds": _num(r.get("over_odds")),
            "under_odds": _num(r.get("under_odds")),
        })
    return out


def ingest_nflverse(csv_path: str | Path, root: Path,
                    seasons: range | None = None,
                    overwrite_games: bool = False) -> dict:
    """Read an nflverse games.csv and write our backfill + closing-line stores
    for the requested seasons (default 2016-2025)."""
    import pandas as pd

    seasons = seasons or range(2016, 2026)
    df = pd.read_csv(csv_path)
    df = df[df["season"].isin(list(seasons))]
    games = games_from_nflverse(df.to_dict("records"))
    return write_history(games, Path(root), "NFL", overwrite_games)


def main(argv: list[str] | None = None) -> None:
    import argparse

    from . import config

    ap = argparse.ArgumentParser(description="Ingest NFL history (results + lines)")
    ap.add_argument("input", help="nflverse games.csv, or sportsoddshistory text")
    ap.add_argument("--format", choices=("nflverse", "sportsoddshistory"),
                    default="nflverse")
    ap.add_argument("--root", default=str(config.REPO_ROOT / "data" / "history"))
    ap.add_argument("--overwrite-games", action="store_true")
    args = ap.parse_args(argv)

    if args.format == "nflverse":
        counts = ingest_nflverse(args.input, Path(args.root),
                                 overwrite_games=args.overwrite_games)
    else:
        games = parse_games(Path(args.input).read_text())
        counts = write_history(games, Path(args.root), "NFL", args.overwrite_games)

    total_g = sum(c["games"] for c in counts.values())
    total_l = sum(c["lines"] for c in counts.values())
    print(f"wrote {total_g} games across {len(counts)} seasons "
          f"({total_l} closing-line rows)")
    for season, c in sorted(counts.items()):
        print(f"  {season}: {c['games']} games, {c['lines']} lines")


if __name__ == "__main__":
    main()
