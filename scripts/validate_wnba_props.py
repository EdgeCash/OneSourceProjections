"""Walk-forward calibration of the WNBA prop rate model
(``project547/models/wnba_props``).

We don't have a deep archive of historical WNBA prop *lines*, so we can't yet
measure edge-vs-market (CLV) — that waits on line data. What we CAN prove now,
honestly, is the model's **distribution calibration**: when it says "62% to go
over 4.5 assists", does that hit ~62% of the time, out of sample?

Method: chronological over all committed logs. For each player-game with enough
prior history, build the rate from PRIOR games only, then at the two half-point
lines straddling the projection (where books actually hang totals) predict
P(over) and record the realized outcome. Aggregate into a reliability table and
compare log-loss / Brier to a naive baseline (the player's prior empirical
over-rate at that line).

Run: PYTHONPATH=. python scripts/validate_wnba_props.py
"""
from __future__ import annotations

import glob
import gzip
import json
import math
from collections import defaultdict

from project547.models import wnba_props as wp

MIN_PRIOR = 6      # games of history before we trust a projection
MIN_MINUTES = 5


def load_logs():
    rows = []
    for f in sorted(glob.glob("data/history/backfill/wnba/*/player_games.jsonl.gz")):
        with gzip.open(f) as fh:
            for line in fh:
                r = json.loads(line)
                if r.get("did_not_play") or (r.get("minutes") or 0) < MIN_MINUTES:
                    continue
                rows.append(r)
    rows.sort(key=lambda r: (r.get("date", ""), r.get("game_id", "")))
    return rows


class Cal:
    def __init__(self, name, bins=10):
        self.name = name
        self.n = 0
        self.ll = 0.0
        self.brier = 0.0
        self.bins = [[0.0, 0.0, 0] for _ in range(bins)]  # sum_p, sum_y, count

    def add(self, p, y):
        p = min(max(p, 1e-6), 1 - 1e-6)
        self.n += 1
        self.ll += -(y * math.log(p) + (1 - y) * math.log(1 - p))
        self.brier += (p - y) ** 2
        b = min(int(p * len(self.bins)), len(self.bins) - 1)
        self.bins[b][0] += p
        self.bins[b][1] += y
        self.bins[b][2] += 1

    def report(self):
        n = max(self.n, 1)
        print(f"\n{self.name}: n={self.n}  LogLoss={self.ll/n:.4f}  Brier={self.brier/n:.4f}")
        print("  bin   pred    emp     n     |Δ|")
        ece = 0.0
        for lo, (sp, sy, c) in zip(range(len(self.bins)), self.bins):
            if not c:
                continue
            pred, emp = sp / c, sy / c
            ece += abs(pred - emp) * c
            print(f"  {lo/len(self.bins):.1f}  {pred:.3f}  {emp:.3f}  {c:5d}  {abs(pred-emp):.3f}")
        print(f"  ECE={ece/n:.4f}")


def main():
    rows = load_logs()
    # per-player chronological series of each stat
    hist = defaultdict(lambda: defaultdict(list))   # pid -> stat -> [values]
    model = Cal("Rate model (NB)")
    base = Cal("Baseline (prior over-rate)")
    for r in rows:
        pid = r["player_id"]
        for market, cfg in wp.MARKETS.items():
            stat = cfg["stat"]
            series = hist[pid][stat]
            if len(series) >= MIN_PRIOR:
                proj = wp.project(series, market)
                actual = float(r.get(stat, 0) or 0)
                for line in (round(proj.proj) - 0.5, round(proj.proj) + 0.5):
                    if line < 0:
                        continue
                    y = 1.0 if actual > line else 0.0
                    model.add(wp.prob_over(proj.proj, line, proj.r), y)
                    # baseline: empirical over-rate at this line over prior games
                    emp = sum(1 for v in series if v > line) / len(series)
                    base.add(emp, y)
        # update history AFTER predicting (walk-forward)
        for market, cfg in wp.MARKETS.items():
            hist[pid][cfg["stat"]].append(float(r.get(cfg["stat"], 0) or 0))
    base.report()
    model.report()
    print("\nLower LogLoss/Brier/ECE = better; 'pred' should track 'emp' down "
          "the reliability table.")


if __name__ == "__main__":
    main()
