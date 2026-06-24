"""Probe which BettingPros sports actually return data, so we can prune the
BP_SPORTS / MODELED_BP_SPORT maps to real coverage.

Run locally where the BP keys + network are live (it can't run in the build
sandbox, which is egress-blocked):

    PYTHONPATH=. python scripts/bp_coverage.py [YYYY-MM-DD]

For each candidate BP sport it prints market count and today's event count, plus
whether a moneyline market resolves — a quick, cheap (cached) coverage map.
Well within the 5k/day call budget.
"""
from __future__ import annotations

import sys
from datetime import date

from project547 import baseline, bp
from project547.clients import bettingpros as bpc

# Everything we might map to BP: the unmodeled groups + the modeled sports,
# plus a few common BP codes worth confirming.
CANDIDATES = sorted({s for s in baseline.BP_SPORTS.values() if s}
                    | set(bp.MODELED_BP_SPORT.values())
                    | {"NCAAB", "TENNIS", "GOLF", "UFC", "NASCAR", "SOCCER",
                       "WNBA", "CFL"})


def main():
    day = sys.argv[1] if len(sys.argv) > 1 else date.today().isoformat()
    print(f"BettingPros coverage probe — {day}\n")
    print(f"{'sport':10} {'markets':>8} {'events':>7} {'moneyline':>10}")
    print("-" * 40)
    for s in CANDIDATES:
        try:
            mk = bpc.markets(s)
            ev = bpc.events(s, day)
            ml = bpc.game_market_ids(s).get("moneyline")
            print(f"{s:10} {len(mk):8} {len(ev):7} {('id ' + str(ml)) if ml else '—':>10}")
        except Exception as e:  # noqa: BLE001 — diagnostic, show every failure
            print(f"{s:10} ERROR {type(e).__name__}: {str(e)[:50]}")
    print("\nPrune project547/baseline.BP_SPORTS and bp.MODELED_BP_SPORT to the "
          "rows with markets/events > 0.")


if __name__ == "__main__":
    main()
