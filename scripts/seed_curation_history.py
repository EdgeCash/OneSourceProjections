"""Seed the curation gate's conviction prior from a production-mode backtest.

Runs the CURRENT model walk-forward over historical slates in *production mode*
(fitted calibration maps + each sport's market_shrink, exactly like the live
pipeline) and writes per-(sport, market) CLV to data/history/curation_seed.json.

The curation layer reads this as a prior for conviction RANKING only — never
stake sizing — and only while a market's live CLV sample is still thin, so it
self-retires as the real ledger accrues. See docs/CURATION_DESIGN.md step 2 and
edge_gate.conviction_prior / blend_conviction. The prior does nothing until
config.CURATION_SEED_ENABLED is turned on.

Usage:
    python scripts/seed_curation_history.py
    python scripts/seed_curation_history.py --sports MLB,NBA --draws 2000
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from project547 import backtest, config  # noqa: E402

# Recent seasons with closing-line coverage in the committed backfill. The
# backtest only counts bets it can match to a closing line, so a generous range
# is safe — unmatched seasons simply contribute nothing.
DEFAULT_SEASONS = {
    "MLB": [2024, 2025, 2026],
    "NBA": [2022, 2023, 2024, 2025],
    "NFL": [2021, 2022, 2023, 2024],
    "NHL": [2022, 2023, 2024, 2025],
    "WNBA": [2023, 2024, 2025],
    "NCAAF": [2022, 2023, 2024],
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sports", default=",".join(DEFAULT_SEASONS))
    ap.add_argument("--draws", type=int, default=3000)
    ap.add_argument("--out", default=str(config.CURATION_SEED_PATH))
    args = ap.parse_args()
    sports = [s.strip().upper() for s in args.sports.split(",") if s.strip()]

    markets = []
    for sk in sports:
        seasons = DEFAULT_SEASONS.get(sk)
        if not seasons:
            print(f"  {sk}: no default seasons, skipping")
            continue
        print(f"Running production-mode backtest: {sk} {seasons} ...")
        try:
            res = backtest.run_game_backtest(
                sk, seasons, draws=args.draws, production_mode=True,
                use_starters=(sk == "MLB"), use_bullpen=(sk == "MLB"),
                use_park=(sk == "MLB"))
        except Exception as e:                       # a missing feed shouldn't kill the rest
            print(f"  {sk}: backtest failed ({e}); skipping")
            continue
        by_mkt = res.get("closing_line", {}).get("clv_by_market", {})
        for mkt, stats in by_mkt.items():
            if stats.get("clv_n"):
                markets.append({"sport": sk, "market": mkt, **stats})
                print(f"  {sk} {mkt}: n={stats['clv_n']} "
                      f"avg_clv={stats['avg_clv']} lb={stats['clv_lb']}")

    payload = {
        "note": "curation conviction prior (docs/CURATION_DESIGN.md step 2)",
        "source": "production-mode walk-forward backtest of the current model",
        "seasons": {s: DEFAULT_SEASONS.get(s) for s in sports},
        "draws": args.draws,
        "markets": markets,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1))
    print(f"\nWrote {out} ({len(markets)} market rows). "
          f"Enable with config.CURATION_SEED_ENABLED = True after review.")


if __name__ == "__main__":
    main()
