"""The real gate: does the QB-adjusted margin BEAT THE CLOSE, not just the
outcome?  Brier/log-loss (validate_qb_epa.py) says the QB signal improves win-
probability calibration; that has been true before while CLV got *worse*. This
scores both models against the committed NFL closing spreads.

For each game (walk-forward, week >= 5) we train the same expanding-window
stacker as validate_qb_epa (points-only, and points+QB-adjusted) on prior
games, predict the margin, and pick ATS vs the closing home spread. We report:
  * ATS%  — win rate against the close (> 52.4% clears the vig)
  * ATS% on games where the model disagrees with the market by >= EDGE_PTS
  * mean CLV proxy — |model margin - market spread| on the side we took, signed
    by whether it covered (positive = our disagreement was right)

Run: PYTHONPATH=. python scripts/validate_qb_clv.py [edge_pts]
"""
from __future__ import annotations

import glob
import gzip
import json
import sys

import numpy as np

import scripts.validate_qb_epa as v

EDGE_PTS = float(sys.argv[1]) if len(sys.argv) > 1 else 1.0
MIN_TRAIN = v.MIN_TRAIN


def load_close_spreads():
    """game_id -> home closing spread (points). event_id already equals the
    nflverse game_id (e.g. 2024_01_BAL_KC)."""
    out = {}
    for f in sorted(glob.glob("data/history/closing_lines/nfl/*.jsonl.gz")):
        with gzip.open(f) as fh:
            for line in fh:
                r = json.loads(line)
                if r.get("market") == "spread" and r.get("side") == "home":
                    out[r["event_id"]] = float(r["line"])
    return out


def _fit(X, y):
    return np.linalg.lstsq(np.array(X), np.array(y), rcond=None)[0]


class ATS:
    def __init__(self, name):
        self.name, self.w, self.l, self.p = name, 0, 0, 0
        self.ew, self.el = 0, 0          # edge-thresholded
        self.clv = []

    def grade(self, model_margin, market_spread, actual_margin):
        # market home spread s: home covers if actual + s > 0. We bet home if
        # our margin implies more home edge than the market (model + s > 0).
        cover_val = actual_margin + market_spread
        pick_home = (model_margin + market_spread) > 0
        disagree = abs(model_margin - (-market_spread))
        if abs(cover_val) < 1e-9:
            self.p += 1
            return
        home_covered = cover_val > 0
        win = (pick_home == home_covered)
        if win:
            self.w += 1
        else:
            self.l += 1
        self.clv.append(disagree if win else -disagree)
        if disagree >= EDGE_PTS:
            if win:
                self.ew += 1
            else:
                self.el += 1

    def report(self):
        tot = self.w + self.l
        ats = 100 * self.w / tot if tot else 0
        et = self.ew + self.el
        eats = 100 * self.ew / et if et else 0
        clv = float(np.mean(self.clv)) if self.clv else 0.0
        print(f"{self.name:<18} ATS {ats:5.1f}% ({self.w}-{self.l}-{self.p})  "
              f"| edge>={EDGE_PTS}pt {eats:5.1f}% (n={et})  | meanCLV {clv:+.2f}")


def main():
    seasons = v.load_seasons()
    spreads = load_close_spreads()
    rows = [r for r in v.build_rows(seasons) if r["game_id"] in spreads]
    print(f"\nNFL QB CLV gate — {len(rows)} games w/ closing spread, "
          f"seasons {sorted(seasons)}, week>={v.TRUST_WEEK}, edge={EDGE_PTS}pt\n")
    pts = ATS("Points (raw)")
    qb = ATS("Points + QBadj")
    Xp, Xq, margins = [], [], []
    for r in rows:
        pm, adj, actual = r["pm"], r["adj"], r["margin"]
        s = spreads[r["game_id"]]
        if len(margins) >= MIN_TRAIN:
            bp, bq = _fit(Xp, margins), _fit(Xq, margins)
            mp = float(np.dot(bp, [1.0, pm]))
            mq = float(np.dot(bq, [1.0, pm, adj]))
        else:
            mp = mq = pm
        pts.grade(mp, s, actual)
        qb.grade(mq, s, actual)
        Xp.append([1.0, pm]); Xq.append([1.0, pm, adj]); margins.append(actual)
    pts.report()
    qb.report()
    print("\n>52.4% ATS clears the vig; compare the two rows. meanCLV>0 means our "
          "disagreements with the close were right on average.")


if __name__ == "__main__":
    main()
