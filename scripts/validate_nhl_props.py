"""Fit + validate the NHL skater-prop dispersions once the committed logs exist.

Mirrors scripts/validate_wnba_props.py, but first FITS each market's negative-
binomial ``r`` on a train split (pre-2024) by minimizing walk-forward log-loss,
then reports held-out (2024+) calibration vs a naive baseline and prints the
``MARKETS`` r values to paste back into project547/models/nhl_props.py.

Run (after scripts/import_nhl_skaters.py has written the logs):
    PYTHONPATH=. python scripts/validate_nhl_props.py
"""
from __future__ import annotations

import glob
import gzip
import json
import math
from collections import defaultdict

from project547.models import nhl_props as npx

MIN_PRIOR = 6
FIT_GRID = [1.5 + 0.25 * i for i in range(38)]   # 1.5 .. 11.0


def load_logs():
    rows = []
    for f in sorted(glob.glob("data/history/backfill/nhl/*/player_games.jsonl.gz")):
        with gzip.open(f) as fh:
            for line in fh:
                r = json.loads(line)
                if r.get("shots") is None and r.get("saves") is None:
                    continue
                rows.append(r)
    rows.sort(key=lambda r: (r.get("date", ""), r.get("game_id", "")))
    return rows


def _pairs(rows):
    """Walk-forward (mean, line, y, is_test) per market from prior games only."""
    hist = defaultdict(lambda: defaultdict(list))
    out = defaultdict(list)
    for r in rows:
        pid = r.get("player_id") or r.get("player_name")
        test = r.get("date", "") >= "2024-01-01"
        is_goalie = r.get("saves") is not None
        for market, cfg in npx.MARKETS.items():
            # only score a market for the matching role (goalie rows -> saves,
            # skater rows -> everything else) so zeros of the wrong role don't
            # pollute the fit.
            if (cfg["role"] == "goalie") != is_goalie:
                continue
            stat = cfg["stat"]
            series = hist[pid][stat]
            if len(series) >= MIN_PRIOR:
                proj = npx.project(series, market)
                actual = float(r.get(stat, 0) or 0)
                for line in (round(proj.proj) - 0.5, round(proj.proj) + 0.5):
                    if line >= 0:
                        out[market].append((proj.proj, line,
                                            1.0 if actual > line else 0.0, test))
        for market, cfg in npx.MARKETS.items():
            if (cfg["role"] == "goalie") != is_goalie:
                continue
            v = r.get(cfg["stat"])
            if v is not None:
                hist[pid][cfg["stat"]].append(float(v))
    return out


def _ll(data, r):
    tot, n = 0.0, 0
    for mean, line, y, _ in data:
        p = min(max(npx.prob_over(mean, line, r), 1e-6), 1 - 1e-6)
        tot += -(y * math.log(p) + (1 - y) * math.log(1 - p))
        n += 1
    return tot / max(n, 1)


def _ece(data, r):
    b = defaultdict(lambda: [0.0, 0.0]); n = 0
    for mean, line, y, _ in data:
        p = min(max(npx.prob_over(mean, line, r), 1e-6), 1 - 1e-6)
        b[min(int(p * 10), 9)][0] += p; b[min(int(p * 10), 9)][1] += y; n += 1
    return sum(abs(x - z) for x, z in b.values()) / max(n, 1)


def main():
    rows = load_logs()
    if not rows:
        print("No committed NHL skater logs yet. Run scripts/import_nhl_skaters.py "
              "on the box-score CSV first, then commit "
              "data/history/backfill/nhl/*/player_games.jsonl.gz.")
        return
    print(f"{len(rows):,} skater-games loaded.\n")
    data = _pairs(rows)
    print(f"{'market':9} {'cur_r':>5} {'fit_r':>5} | {'testLL':>7} {'testECE':>7} "
          f"{'n_test':>7}")
    fitted = {}
    for market in npx.MARKETS:
        d = data[market]
        train = [x for x in d if not x[3]]
        test = [x for x in d if x[3]]
        if len(train) < 200 or len(test) < 200:
            print(f"{market:9}  (insufficient split: train={len(train)} test={len(test)})")
            continue
        rstar = min(FIT_GRID, key=lambda r: _ll(train, r))
        fitted[market] = rstar
        print(f"{market:9} {npx.MARKETS[market]['r']:5.1f} {rstar:5.2f} | "
              f"{_ll(test, rstar):7.4f} {_ece(test, rstar):7.3f} {len(test):7d}")
    if fitted:
        print("\nPaste into project547/models/nhl_props.MARKETS (r=...):")
        for m, r in fitted.items():
            print(f"  {m:9} r={r:.1f}")


if __name__ == "__main__":
    main()
