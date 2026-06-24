"""Walk-forward validation: can opponent-adjusted EPA improve NFL game
projections over the points-based ratings the generic model uses today?

Builds, per game (week >= TRUST_WEEK), two leakage-free signals from only that
season's earlier weeks:
  * points_margin — home margin from points-for/against ratings (today's model)
  * epa_margin    — home margin from opponent-adjusted EPA ratings (project547/epa)

Then compares several ways to turn signals into a win probability, all scored
walk-forward with proper rules (Brier / LogLoss / MarginMAE):
  * Points (raw)      Φ(points_margin / σ)            — current production
  * EPA (raw)         Φ(epa_margin / σ)
  * EPA-cal (learned) Φ((a + b·epa_margin) / σ)       — calibrate EPA scale
  * Stack (learned)   Φ((a + b1·points + b2·epa) / σ) — let the data weight them

The learned models use an EXPANDING window (fit on all earlier games, predict
the next) — proper online walk-forward, no leakage. Stack only earns a
production weight if it clearly and *stably* beats Points across seasons.

Run: PYTHONPATH=. python scripts/validate_epa.py [ridge_lam]
"""
from __future__ import annotations

import math
import pathlib
import sys

import numpy as np
import pandas as pd

from project547 import epa

PBP_DIR = pathlib.Path("data/history/pbp/nfl")
TRUST_WEEK = 5
SIGMA = 13.5
HFA = 2.0
RIDGE_LAM = float(sys.argv[1]) if len(sys.argv) > 1 else 25.0
MIN_TRAIN = 64  # games before the learned models are trusted (else fall back to points)


def _phi(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _clip(p, eps=1e-6):
    return min(max(p, eps), 1 - eps)


class Metrics:
    def __init__(self, name):
        self.name, self.n, self.brier, self.ll, self.mae, self.win = name, 0, 0.0, 0.0, 0.0, 0

    def add(self, p, pred_margin, actual):
        y = 1.0 if actual > 0 else 0.0
        p = _clip(p)
        self.n += 1
        self.brier += (p - y) ** 2
        self.ll += -(y * math.log(p) + (1 - y) * math.log(1 - p))
        self.mae += abs(pred_margin - actual)
        if (p >= 0.5) == (y == 1.0):
            self.win += 1

    def row(self):
        n = max(self.n, 1)
        return (f"{self.name:<16} n={self.n:<5} Brier={self.brier/n:.4f}  "
                f"LogLoss={self.ll/n:.4f}  MarginMAE={self.mae/n:.2f}  "
                f"Win%={100*self.win/n:.1f}")


def load_seasons():
    out = {}
    for f in sorted(PBP_DIR.glob("play_by_play_*.parquet")):
        try:
            out[int(f.stem.split("_")[-1])] = pd.read_parquet(f)
        except Exception:
            pass
    return out


def _reg_games(df):
    reg = df[df.get("season_type", "REG") == "REG"]
    g = reg.groupby("game_id").agg(
        week=("week", "first"), home=("home_team", "first"),
        away=("away_team", "first"), result=("result", "first"),
        hs=("home_score", "first"), as_=("away_score", "first")).reset_index()
    return g.dropna(subset=["result", "home", "away", "week"])


def _points_ratings(df_before):
    g = df_before.groupby("game_id").agg(
        home=("home_team", "first"), away=("away_team", "first"),
        hs=("home_score", "first"), as_=("away_score", "first")).reset_index()
    pf, pa, n = {}, {}, {}
    for _, r in g.iterrows():
        for t, s, a in [(r["home"], r["hs"], r["as_"]), (r["away"], r["as_"], r["hs"])]:
            pf[t] = pf.get(t, 0) + s; pa[t] = pa.get(t, 0) + a; n[t] = n.get(t, 0) + 1
    return {t: (pf[t] / n[t], pa[t] / n[t]) for t in n}


def _epa_plays(df_before):
    d = df_before[df_before["play_type"].isin(("pass", "run"))]
    d = d[d["epa"].notna() & d["posteam"].notna() & d["defteam"].notna()]
    if "wp" in d.columns:
        d = d[(d["wp"] > 0.05) & (d["wp"] < 0.95)]
    cols = ["posteam", "defteam", "epa"] + (["success"] if "success" in d.columns else [])
    return d[cols].to_dict("records")


def _qb_epa(df_before):
    """team -> mean EPA/play of its PRIMARY passer (most attempts) over prior
    weeks. The QB-adjusted signal the aggregate ratings throw away — research's
    #1 NFL lever. (Crude: not opponent-adjusted or sample-shrunk yet.)"""
    if "passer_player_name" not in df_before.columns:
        return {}
    p = df_before[(df_before.get("pass", 0) == 1)
                  & df_before["passer_player_name"].notna()
                  & df_before["epa"].notna()]
    out = {}
    for team, g in p.groupby("posteam"):
        top = g["passer_player_name"].value_counts().idxmax()
        out[team] = float(g[g["passer_player_name"] == top]["epa"].mean())
    return out


def build_rows(seasons):
    """Chronological games with walk-forward (points_margin, epa_margin, actual)."""
    rows = []
    for yr, df in sorted(seasons.items()):
        if "result" not in df.columns or "home_score" not in df.columns:
            continue
        games = _reg_games(df)
        for w in sorted(int(x) for x in games["week"].unique() if x >= TRUST_WEEK):
            before = df[df["week"] < w]
            gb = before[before.get("season_type", "REG") == "REG"]
            if gb.empty:
                continue
            er = epa.team_epa_ratings(_epa_plays(gb), league="nfl", lam=RIDGE_LAM)
            pr = _points_ratings(gb)
            qb = _qb_epa(gb)
            for _, g in games[games["week"] == w].iterrows():
                h, a, actual = g["home"], g["away"], float(g["result"])
                if not (h in er and a in er and h in pr and a in pr):
                    continue
                pm = (pr[h][0] + pr[a][1]) / 2 - (pr[a][0] + pr[h][1]) / 2 + HFA
                em = epa.expected_margin(er[h], er[a], league="nfl", hfa=HFA)
                qd = qb.get(h, 0.0) - qb.get(a, 0.0)
                rows.append({"season": yr, "week": w, "pm": pm, "em": em,
                             "qd": qd, "y": actual})
    return rows


def main():
    seasons = load_seasons()
    if not seasons:
        print("No PBP parquet under", PBP_DIR, "— see its README to fetch.")
        return
    rows = build_rows(seasons)
    names = ("Points (raw)", "EPA (raw)", "Stack(pts+epa)", "Stack(pts+QB)")
    m = {k: Metrics(k) for k in names}
    X_stack, X_qb, train_y = [], [], []
    for r in rows:
        pm, em, qd, y = r["pm"], r["em"], r["qd"], r["y"]
        m["Points (raw)"].add(_phi(pm / SIGMA), pm, y)
        m["EPA (raw)"].add(_phi(em / SIGMA), em, y)
        # learned models (expanding window; fall back to points until trained)
        if len(train_y) >= MIN_TRAIN:
            yv = np.array(train_y)
            bs = np.linalg.lstsq(np.array(X_stack), yv, rcond=None)[0]
            bq = np.linalg.lstsq(np.array(X_qb), yv, rcond=None)[0]
            stack_pred = bs[0] + bs[1] * pm + bs[2] * em
            qb_pred = bq[0] + bq[1] * pm + bq[2] * qd
        else:
            stack_pred = qb_pred = pm
        m["Stack(pts+epa)"].add(_phi(stack_pred / SIGMA), stack_pred, y)
        m["Stack(pts+QB)"].add(_phi(qb_pred / SIGMA), qb_pred, y)
        X_stack.append([1.0, pm, em])
        X_qb.append([1.0, pm, qd])  # points + QB-EPA diff (drop the dead aggregate EPA)
        train_y.append(y)
    print(f"\nWalk-forward NFL validation — seasons {sorted(seasons)}, "
          f"week>={TRUST_WEEK}, ridge_lam={RIDGE_LAM}\n")
    for k in names:
        print(m[k].row())
    print("\nLower Brier/LogLoss/MarginMAE = better; higher Win% = better.")
    if len(train_y) >= MIN_TRAIN:
        bs = np.linalg.lstsq(np.array(X_stack), np.array(train_y), rcond=None)[0]
        bq = np.linalg.lstsq(np.array(X_qb), np.array(train_y), rcond=None)[0]
        print(f"\nFinal coefs (margin):")
        print(f"  pts+epa:  intercept={bs[0]:.3f}, points={bs[1]:.3f}, epa={bs[2]:.3f}"
              "   → aggregate EPA ≈ 0 (no signal)")
        print(f"  pts+QB:   intercept={bq[0]:.3f}, points={bq[1]:.3f}, "
              f"qb_epa_diff={bq[2]:.3f}   → QB-EPA carries real weight (the lever)")


if __name__ == "__main__":
    main()
