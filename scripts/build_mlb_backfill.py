"""Assemble ``backfill/mlb/<season>/games.json.gz`` from the committed season
results + linescores (+ closing odds for the betting total), matching the rich
2025 schema that :func:`project547.teamstats._mlb_team_games` consumes.

Why this exists: the 2026 season was imported as separate ``results/mlb/<season>
.jsonl.gz`` and ``backfill/mlb/<season>/linescores.json.gz`` files but never
assembled into the ``games.json.gz`` the model actually reads. So
``team_games('MLB')`` silently fell back to 2025 data all season — every MLB
projection, the NRFI rates, and the matchup cards ran on a stale prior year.
This rebuilds the file so the current season flows through.

The join: linescores carry first-inning / first-5 runs but NO team names, so
they attach to results (which carry teams + scores) by game_pk. Games without a
matching linescore still get a row (scores only); first-inning fields are null.
Spring-training games (before the season's first linescore date) are excluded.

Idempotent and repeatable — safe to re-run for any season that has results +
linescores. In production (statsapi reachable) ``--refresh`` additionally pulls
recent finals + first-inning linescores from statsapi and merges them, so the
file extends past the committed import cutoff and stays current.

    python scripts/build_mlb_backfill.py --season 2026
    python scripts/build_mlb_backfill.py --season 2026 --refresh   # prod only
"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import sys
from collections import defaultdict
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from project547 import teams  # noqa: E402
from project547.config import REPO_ROOT  # noqa: E402

log = logging.getLogger("build_mlb_backfill")
H = REPO_ROOT / "data" / "history"


def _pk(game_id) -> str:
    """Extract the numeric game_pk from a result game_id ('MLB-STATS-825080')."""
    return str(game_id).split("-")[-1]


def load_results(season: int) -> list[dict]:
    path = H / "results" / "mlb" / f"{season}.jsonl.gz"
    if not path.exists():
        return []
    out = []
    with gzip.open(path, "rt") as f:
        for line in f:
            if line.strip():
                out.append(json.loads(line))
    return out


def load_linescores(season: int) -> dict:
    path = H / "backfill" / "mlb" / str(season) / "linescores.json.gz"
    if not path.exists():
        return {}
    with gzip.open(path, "rt") as f:
        return {str(k): v for k, v in json.load(f).items()}


def load_total_lines(season: int) -> dict:
    """{(date, frozenset(abbrs)): consensus total line} from the game odds —
    the only odds-derived field we trust to backfill (unambiguous median line).
    Returns {} when the odds file is absent."""
    path = H / "bp_odds" / f"bp_game_odds_{season}.jsonl.gz"
    if not path.exists():
        return {}
    lines: dict = defaultdict(list)
    with gzip.open(path, "rt") as f:
        for line in f:
            r = json.loads(line)
            if r.get("market_slug") != "total" or r.get("line") is None:
                continue
            key = (r.get("date"), frozenset(r.get("abbrs", [])))
            lines[key].append(float(r["line"]))
    return {k: median(v) for k, v in lines.items() if v}


def _derive_nrfi(ls: dict) -> bool | None:
    if ls.get("nrfi") is not None:
        return bool(ls["nrfi"])
    fa, fh = ls.get("f1_away"), ls.get("f1_home")
    if fa is None or fh is None:
        return None
    return (fa == 0) and (fh == 0)


def _winner(home_abbr, away_abbr, hs, as_):
    if hs is None or as_ is None or hs == as_:
        return None
    return home_abbr if hs > as_ else away_abbr


def assemble(season: int) -> list[dict]:
    results = load_results(season)
    lscores = load_linescores(season)
    totals = load_total_lines(season)
    if not results:
        log.warning("no results for %s", season)
        return []

    # Season start = earliest linescore date (regular season; excludes spring).
    ls_dates = [v.get("game_date") for v in lscores.values() if v.get("game_date")]
    season_start = min(ls_dates) if ls_dates else None

    rows, n_with_f1, n_total = [], 0, 0
    for r in results:
        date = str(r.get("date"))[:10]
        if season_start and date < season_start:
            continue  # spring training
        pk = _pk(r.get("game_id"))
        home = teams.canon("MLB", r.get("home_team", ""))
        away = teams.canon("MLB", r.get("away_team", ""))
        hs, as_ = r.get("home_score"), r.get("away_score")
        if hs is None or as_ is None:
            continue
        ls = lscores.get(pk, {})
        f1_away, f1_home = ls.get("f1_away"), ls.get("f1_home")
        f5_away, f5_home = ls.get("f5_away"), ls.get("f5_home")
        if f1_away is not None and f1_home is not None:
            n_with_f1 += 1
        tot = totals.get((date, frozenset({home, away})))
        if tot is not None:
            n_total += 1
        rows.append({
            "date": date,
            "game_pk": int(pk) if pk.isdigit() else pk,
            "away_team": away, "home_team": home,
            "away_score": as_, "home_score": hs,
            "total_runs": hs + as_,
            "ml_winner": _winner(home, away, hs, as_),
            "rl_margin": hs - as_,
            "rl_favorite_covered": None,  # needs favorite ID; left null for 2026
            "f5_away": f5_away, "f5_home": f5_home,
            "f5_winner": _winner(home, away, f5_home, f5_away)
            if (f5_home is not None and f5_away is not None) else None,
            "f1_away": f1_away, "f1_home": f1_home,
            "nrfi": _derive_nrfi(ls),
            "total": tot,
            "away_total": None, "home_total": None,
            # Per-inning detail isn't in the linescore summary; the explicit
            # f1_*/f5_* fields above are what the model reads.
            "innings": [],
        })
    rows.sort(key=lambda x: x["date"])
    log.info("season %s: %d games (%d with first-inning, %d with total line), "
             "%s -> %s", season, len(rows), n_with_f1, n_total,
             rows[0]["date"] if rows else "-", rows[-1]["date"] if rows else "-")
    return rows


def write(season: int, rows: list[dict]) -> Path:
    out = H / "backfill" / "mlb" / str(season) / "games.json.gz"
    out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out, "wt") as f:
        json.dump(rows, f)
    return out


def refresh_from_statsapi(season: int, rows: list[dict], days: int = 10) -> list[dict]:
    """Best-effort: pull recent finals + first-inning linescores from statsapi
    (production only — the sandbox proxy blocks it) and merge any games newer
    than the committed import, keyed by game_pk. Never raises."""
    try:
        from datetime import date as _D, timedelta

        from project547.clients import mlb_statsapi
    except Exception as e:  # pragma: no cover
        log.warning("statsapi refresh unavailable: %s", e)
        return rows
    have = {r["game_pk"] for r in rows}
    today = _D.today()
    added = 0
    for i in range(days):
        d = (today - timedelta(days=i)).isoformat()
        try:
            recent = mlb_statsapi.final_linescores(d)
        except Exception as e:  # pragma: no cover
            log.warning("statsapi %s: %s", d, e)
            continue
        for g in recent:
            if g["game_pk"] in have:
                continue
            home = teams.canon("MLB", g["home_team"])
            away = teams.canon("MLB", g["away_team"])
            hs, as_ = g["home_score"], g["away_score"]
            rows.append({
                "date": g["date"], "game_pk": g["game_pk"],
                "away_team": away, "home_team": home,
                "away_score": as_, "home_score": hs,
                "total_runs": hs + as_, "ml_winner": _winner(home, away, hs, as_),
                "rl_margin": hs - as_, "rl_favorite_covered": None,
                "f5_away": g.get("f5_away"), "f5_home": g.get("f5_home"),
                "f5_winner": _winner(home, away, g.get("f5_home"), g.get("f5_away"))
                if g.get("f5_home") is not None else None,
                "f1_away": g.get("f1_away"), "f1_home": g.get("f1_home"),
                "nrfi": _derive_nrfi(g), "total": None,
                "away_total": None, "home_total": None, "innings": [],
            })
            have.add(g["game_pk"])
            added += 1
    if added:
        rows.sort(key=lambda x: x["date"])
        log.info("statsapi refresh added %d games (through %s)",
                 added, rows[-1]["date"])
    return rows


def _load_existing(season: int) -> list[dict]:
    path = H / "backfill" / "mlb" / str(season) / "games.json.gz"
    if not path.exists():
        return []
    with gzip.open(path, "rt") as f:
        return json.load(f)


def _merge_by_pk(*row_lists: list[dict]) -> list[dict]:
    """Union rows by game_pk; later lists win on conflict (fresher data)."""
    by_pk: dict = {}
    for rows in row_lists:
        for r in rows:
            by_pk[r["game_pk"]] = r
    return sorted(by_pk.values(), key=lambda r: (str(r["date"]), str(r["game_pk"])))


def refresh_current_season(season: int, days: int = 45) -> int:
    """Production entry point for the daily job: rebuild the current season's
    games.json.gz from the committed base, the prior accumulation, and (when
    statsapi is reachable) recent finals — so the file monotonically extends
    past the import cutoff and the model never goes stale again. Returns the
    game count written. Idempotent."""
    rows = _merge_by_pk(assemble(season), _load_existing(season))
    rows = refresh_from_statsapi(season, rows, days=days)
    write(season, rows)
    return len(rows)


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--refresh", action="store_true",
                    help="also pull recent games from statsapi (production only)")
    args = ap.parse_args()
    rows = assemble(args.season)
    if args.refresh:
        rows = refresh_from_statsapi(args.season, rows)
    if not rows:
        log.warning("nothing to write for %s", args.season)
        return
    path = write(args.season, rows)
    log.info("wrote %d games -> %s", len(rows), path)


if __name__ == "__main__":
    main()
