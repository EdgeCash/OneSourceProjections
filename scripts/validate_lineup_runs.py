"""Walk-forward validation of the lineup-level offense engine (roadmap T2.2 / T2.2b).

Compares totals MAE and moneyline Brier of the lineup-blended MLB run model vs
the team-rate baseline, on exactly the games with a posted lineup, sweeping
config.LINEUP_BLEND — for two per-batter wOBA sources:

  proxy    — the AVG/SLG name-keyed pseudo-wOBA the pipeline shipped with (T2.2)
  statcast — Statcast wOBA blended with xwOBA, PA-shrunk, by player id (T2.2b)

Historical lineups (name + player id) are fetched by game_pk and cached to the
scratchpad so re-runs are instant/resumable.

Usage: python scripts/validate_lineup_runs.py [season] [n_games] [start_offset]

Note: per-batter wOBA uses season-aggregate stats (mild in-season lookahead the
live as-of feed avoids) — a directional read on which SOURCE and blend help.
"""
import json
import sys
from pathlib import Path

import numpy as np

from project547 import backtest as bt
from project547 import batwoba, config, pipeline
from project547.clients import mlb_statsapi
from project547.names import normalize

SEASON = int(sys.argv[1]) if len(sys.argv) > 1 else 2024
N_GAMES = int(sys.argv[2]) if len(sys.argv) > 2 else 500
START = int(sys.argv[3]) if len(sys.argv) > 3 else 300
CACHE = Path("/tmp/claude-0/-home-user-OneSourceProjections/"
             "9efcf663-6afa-5006-be22-a5d4da8d7bac/scratchpad") \
    / f"lineup_ids_{SEASON}.json"
CACHE.parent.mkdir(parents=True, exist_ok=True)


def fetch_lineups(season: int):
    games = [g for g in bt._mlb_games([season], use_results_2026=True)
             if g.get("game_pk")][START:START + N_GAMES]
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    for i, g in enumerate(games):
        pk = str(g["game_pk"])
        if pk not in cache:
            try:
                lu = mlb_statsapi.batting_order(int(pk))
                cache[pk] = {s: [[p.get("name"), p.get("player_id")]
                                 for p in lu.get(s, [])]
                             for s in ("home", "away")} if lu else {}
            except Exception:
                cache[pk] = {}
            if i % 25 == 0:
                CACHE.write_text(json.dumps(cache))
                print(f"  fetched {i}/{len(games)} lineups", flush=True)
    CACHE.write_text(json.dumps(cache))
    return games, cache


def build_table(games, cache, mode: str, proxy: dict):
    table, pkset = {}, set()
    for g in games:
        pk = g["game_pk"]
        lus = cache.get(str(pk), {})
        has = False
        for side in ("home", "away"):
            batters = [(pid, normalize(nm or "")) for nm, pid in (lus.get(side) or [])]
            if not batters:
                continue
            if mode == "proxy":
                ws = [proxy[nm] for _pid, nm in batters if nm in proxy]
                w = sum(ws) / len(ws) if ws else None
            else:  # statcast (by id) with proxy fallback
                w = batwoba.lineup_woba(batters, SEASON, proxy=proxy)
            if w is not None:
                table[(pk, side)] = w
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
    print(f"Fetching lineups for {SEASON} (games {START}..{START + N_GAMES})...", flush=True)
    games, cache = fetch_lineups(SEASON)
    proxy = pipeline._batter_woba_map(pipeline._batter_table(SEASON))
    for mode in ("proxy", "statcast"):
        table, pkset = build_table(games, cache, mode, proxy)
        print(f"\n=== source={mode}  lineup entries={len(table)}  "
              f"games={len(pkset)} ===")
        print(f"{'blend':>6} {'totMAE':>8} {'MLBrier':>8} {'n':>5}")
        for blend in [0.0, 0.35, 0.5, 0.7, 1.0]:
            r = bt.run_game_backtest("MLB", [SEASON], detail=True,
                                     lineup_woba_table=table, lineup_blend=blend)
            mae, br, n = mae_brier(r["games"], pkset)
            print(f"{blend:>6.2f} {mae:>8.3f} {br:>8.4f} {n:>5}", flush=True)


if __name__ == "__main__":
    main()
