"""Harvest home-plate umpire scoring/strikeout tendencies for a season into a
committed table the live pipeline reads.

Umpires call meaningfully different strike zones: a tight zone yields more walks
and runs, a wide one more strikeouts and fewer runs. The effect per game is
small but real and persistent enough to be worth surfacing as game context and
(conservatively) nudging the strikeout environment.

For every completed regular-season game we pull the boxscore once (the only
endpoint carrying both the officials block and team pitching K/BB totals),
attribute the game's total runs / strikeouts / walks to its home-plate umpire,
then aggregate per ump. Indexes are the ump's per-game average over the league
average, shrunk toward 1.0 by the ump's game count so a light-workload ump can't
swing on a handful of games.

Writes: data/history/mlb_umpires.json
  {"season", "generated_utc", "league": {runs_pg, k_pg, bb_pg, games},
   "umpires": {name: {id, games, runs_pg, k_pg, bb_pg,
                      runs_idx, k_idx, bb_idx}}}

Usage:
    python scripts/build_mlb_umpires.py [--season 2026] [--start 2026-03-01]
                                        [--end 2026-07-01]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date as _date
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from project547.config import REPO_ROOT  # noqa: E402

BASE = "https://statsapi.mlb.com/api/v1"
OUT = REPO_ROOT / "data" / "history" / "mlb_umpires.json"
SHRINK_GAMES = 60.0   # games of "league-average" prior; a 30-game ump is ~1/3 shrunk


def _completed_game_pks(start: str, end: str, sess: requests.Session) -> list[int]:
    r = sess.get(f"{BASE}/schedule", params={
        "sportId": 1, "startDate": start, "endDate": end, "gameType": "R"},
        timeout=30)
    r.raise_for_status()
    pks = []
    for day in r.json().get("dates", []):
        for g in day.get("games", []):
            if g.get("status", {}).get("codedGameState") == "F":
                pks.append(g["gamePk"])
    return pks


def _hp_ump(box: dict) -> dict | None:
    for o in box.get("officials", []) or []:
        if (o.get("officialType") or "").lower() == "home plate":
            p = o.get("official", {}) or {}
            if p.get("fullName"):
                return {"name": p["fullName"], "id": p.get("id")}
    return None


def _game_totals(box: dict) -> tuple[int, int, int] | None:
    runs = ks = bbs = 0
    for side in ("home", "away"):
        ts = box.get("teams", {}).get(side, {}).get("teamStats", {})
        bat, pit = ts.get("batting", {}), ts.get("pitching", {})
        if bat.get("runs") is None or pit.get("strikeOuts") is None:
            return None
        runs += bat.get("runs") or 0
        ks += pit.get("strikeOuts") or 0
        bbs += pit.get("baseOnBalls") or 0
    return runs, ks, bbs


def _finalize(agg: dict, last_date: str, season: int) -> dict:
    """Compute league averages + shrunk indexes from raw per-ump sums."""
    tot = {"runs": 0, "ks": 0, "bbs": 0, "games": 0}
    for a in agg.values():
        for k in ("runs", "ks", "bbs"):
            tot[k] += a[k]
        tot["games"] += a["games"]
    g = max(tot["games"], 1)
    lg = {"runs_pg": tot["runs"] / g, "k_pg": tot["ks"] / g,
          "bb_pg": tot["bbs"] / g, "games": tot["games"]}

    def _idx(per_game: float, league: float, n: int) -> float:
        if league <= 0:
            return 1.0
        raw = per_game / league
        w = n / (n + SHRINK_GAMES)
        return round(1.0 + (raw - 1.0) * w, 4)

    umps = {}
    for name, a in agg.items():
        n = a["games"]
        rpg, kpg, bpg = a["runs"] / n, a["ks"] / n, a["bbs"] / n
        umps[name] = {
            "id": a["id"], "games": n,
            "runs_pg": round(rpg, 3), "k_pg": round(kpg, 3),
            "bb_pg": round(bpg, 3),
            "runs_idx": _idx(rpg, lg["runs_pg"], n),
            "k_idx": _idx(kpg, lg["k_pg"], n),
            "bb_idx": _idx(bpg, lg["bb_pg"], n),
            # raw sums kept so the table can be extended incrementally
            "_runs": a["runs"], "_ks": a["ks"], "_bbs": a["bbs"],
        }
    return {"season": season, "generated_utc": _date.today().isoformat(),
            "last_date": last_date,
            "league": {k: round(v, 3) if isinstance(v, float) else v
                       for k, v in lg.items()},
            "umpires": dict(sorted(umps.items()))}


def _load_raw() -> tuple[dict, str | None]:
    """Existing per-ump raw sums + the last date already counted, for an
    incremental extend. Empty if there's no committed table yet."""
    if not OUT.exists():
        return {}, None
    try:
        tbl = json.loads(OUT.read_text())
    except Exception:
        return {}, None
    agg = {}
    for name, u in tbl.get("umpires", {}).items():
        # tolerate an older table written without raw sums (recompute from pg)
        runs = u.get("_runs", round(u.get("runs_pg", 0) * u.get("games", 0)))
        ks = u.get("_ks", round(u.get("k_pg", 0) * u.get("games", 0)))
        bbs = u.get("_bbs", round(u.get("bb_pg", 0) * u.get("games", 0)))
        agg[name] = {"id": u.get("id"), "games": u.get("games", 0),
                     "runs": runs, "ks": ks, "bbs": bbs}
    return agg, tbl.get("last_date")


def build(season: int, start: str, end: str, agg: dict | None = None) -> dict:
    sess = requests.Session()
    pks = _completed_game_pks(start, end, sess)
    print(f"{len(pks)} completed games {start}..{end}")
    agg = agg if agg is not None else {}
    for i, pk in enumerate(pks):
        try:
            box = sess.get(f"{BASE}/game/{pk}/boxscore", timeout=30).json()
        except Exception:
            continue
        ump = _hp_ump(box)
        totals = _game_totals(box)
        if not ump or not totals:
            continue
        runs, ks, bbs = totals
        a = agg.setdefault(ump["name"], {"id": ump["id"], "games": 0,
                                         "runs": 0, "ks": 0, "bbs": 0})
        a["games"] += 1
        a["runs"] += runs
        a["ks"] += ks
        a["bbs"] += bbs
        if (i + 1) % 200 == 0:
            print(f"  ...{i + 1}/{len(pks)}")

    return _finalize(agg, end, season)


def write(table: dict) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(table, separators=(",", ":"), sort_keys=True))


def refresh_current_season(season: int) -> int:
    """Extend the committed table with games played since it was last built
    (or build it from the season start if absent). Cheap in steady state — it
    only fetches boxscores for the new days. Returns the resulting ump count."""
    agg, last = _load_raw()
    start = _next_day(last) if last else f"{season}-03-01"
    end = _date.today().isoformat()
    if start > end:
        return len(agg)
    table = build(season, start, end, agg=agg)
    write(table)
    return len(table["umpires"])


def _next_day(iso: str) -> str:
    from datetime import timedelta
    y, m, d = map(int, iso.split("-"))
    return (_date(y, m, d) + timedelta(days=1)).isoformat()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=_date.today().year)
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--refresh", action="store_true",
                    help="extend the existing table with new games only")
    args = ap.parse_args()
    season = args.season
    if args.refresh and not (args.start or args.end):
        n = refresh_current_season(season)
        print(f"Refreshed {OUT}: {n} umpires")
        return
    start = args.start or f"{season}-03-01"
    end = args.end or _date.today().isoformat()
    table = build(season, start, end)
    write(table)
    n = len(table["umpires"])
    print(f"Wrote {OUT}: {n} umpires, league {table['league']}, "
          f"{OUT.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
