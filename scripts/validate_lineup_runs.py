"""Walk-forward validation of the lineup-level offense engine (roadmap T2.2).

Compares totals MAE and moneyline Brier of the lineup-blended MLB run model vs
the team-rate baseline, on exactly the games with a posted lineup, by sweeping
config.LINEUP_BLEND. Historical lineups are fetched by game_pk (StatsAPI) and
cached to the scratchpad so re-runs are instant and resumable.

Usage: python scripts/validate_lineup_runs.py [season] [n_games] [start_offset]

Note: per-batter wOBA uses the season-aggregate AVG/SLG proxy (the same
_batter_woba_map the live pipeline uses), so this is a *directional* read on
whether the lineup signal helps — mild in-season lookahead, acceptable for a
mechanism check. If it clears here, the next step is a strict as-of wOBA feed.
"""
import json
import sys
from pathlib import Path

import numpy as np

from project547 import backtest as bt
from project547 import config, pipeline
from project547.clients import mlb_statsapi
from project547.names import normalize

SEASON = int(sys.argv[1]) if len(sys.argv) > 1 else 2024
N_GAMES = int(sys.argv[2]) if len(sys.argv) > 2 else 500
START = int(sys.argv[3]) if len(sys.argv) > 3 else 400   # skip cold-start games
CACHE = Path("/tmp/claude-0/-home-user-OneSourceProjections/"
             "9efcf663-6afa-5006-be22-a5d4da8d7bac/scratchpad") \
    / f"lineup_cache_{SEASON}.json"
CACHE.parent.mkdir(parents=True, exist_ok=True)


def build_table(season: int):
    games = [g for g in bt._mlb_games([season], use_results_2026=True)
             if g.get("game_pk")]
    games = games[START:START + N_GAMES]
    bq = pipeline._batter_woba_map(pipeline._batter_table(season))
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    for i, g in enumerate(games):
        pk = str(g["game_pk"])
        if pk not in cache:
            try:
                lu = mlb_statsapi.batting_order(int(pk))
                cache[pk] = ({s: [p["name"] for p in lu.get(s, [])]
                              for s in ("home", "away")} if lu else {})
            except Exception:
                cache[pk] = {}
            if i % 25 == 0:
                CACHE.write_text(json.dumps(cache))
                print(f"  fetched {i}/{len(games)} lineups", flush=True)
    CACHE.write_text(json.dumps(cache))

    table, pkset = {}, set()
    for g in games:
        pk = g["game_pk"]
        lus = cache.get(str(pk), {})
        has = False
        for side in ("home", "away"):
            names = lus.get(side) or []
            ws = [bq[normalize(n)] for n in names if normalize(n) in bq]
            if ws:
                table[(pk, side)] = sum(ws) / len(ws)
                has = True
        if has:
            pkset.add(pk)
    return table, pkset


def mae_brier(rows, pkset):
    tot = [abs(r["proj_total"] - (r["home_score"] + r["away_score"]))
           for r in rows if r.get("game_pk") in pkset]
    br = [(min(max(r["home_win_prob"], 1e-6), 1 - 1e-6) - r["home_won"]) ** 2
          for r in rows if r.get("game_pk") in pkset and r.get("home_won") is not None]
    return (np.mean(tot) if tot else float("nan"),
            np.mean(br) if br else float("nan"), len(tot))


def main():
    print(f"Building lineup wOBA table for {SEASON} "
          f"(games {START}..{START + N_GAMES})...", flush=True)
    table, pkset = build_table(SEASON)
    print(f"lineup entries: {len(table)}  games with lineup: {len(pkset)}\n", flush=True)
    print(f"{'blend':>6} {'totMAE':>8} {'MLBrier':>8} {'n':>5}")
    for blend in [0.0, 0.3, 0.5, 0.7, 1.0]:
        r = bt.run_game_backtest("MLB", [SEASON], detail=True,
                                 lineup_woba_table=table, lineup_blend=blend)
        mae, br, n = mae_brier(r["games"], pkset)
        print(f"{blend:>6.1f} {mae:>8.3f} {br:>8.4f} {n:>5}", flush=True)


if __name__ == "__main__":
    main()
