"""Does an opponent-adjusted, shrunk QB signal beat the crude primary-passer
proxy (and the points baseline)?  Walk-forward, leakage-free, same rules as
scripts/validate_epa.py — this is the QB-feature bake-off feeding roadmap
Stage 4 item 10.

Variants scored per game (week >= TRUST_WEEK), all from that season's earlier
weeks only:
  * Points (raw)              current production baseline
  * Stack(pts+QBcrude)        + primary passer's RAW mean EPA diff (today's PoC)
  * Stack(pts+QBadj)          + opponent-adjusted, ridge-shrunk passer EPA diff
  * Stack(pts+QBadj+cpoe)     + adjusted passer EPA diff and CPOE diff

Run: PYTHONPATH=. python scripts/validate_qb_epa.py [ridge_lam_qb]
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
LAM_QB = float(sys.argv[1]) if len(sys.argv) > 1 else 60.0
MIN_TRAIN = 64


def _phi(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _clip(p, eps=1e-6):
    return min(max(p, eps), 1 - eps)


class Metrics:
    def __init__(self, name):
        self.name, self.n, self.brier, self.ll, self.win = name, 0, 0.0, 0.0, 0

    def add(self, p, y):
        p = _clip(p)
        self.n += 1
        self.brier += (p - y) ** 2
        self.ll += -(y * math.log(p) + (1 - y) * math.log(1 - p))
        if (p >= 0.5) == (y == 1.0):
            self.win += 1

    def row(self):
        n = max(self.n, 1)
        return (f"{self.name:<20} n={self.n:<5} Brier={self.brier/n:.4f}  "
                f"LogLoss={self.ll/n:.4f}  Win%={100*self.win/n:.1f}")


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
        away=("away_team", "first"), result=("result", "first")).reset_index()
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


def _pass_plays(df_before):
    p = df_before[(df_before.get("pass", 0) == 1)
                  & df_before["passer_player_id"].notna()
                  & df_before["defteam"].notna()
                  & df_before["epa"].notna()]
    cols = ["passer_player_id", "defteam", "epa"]
    if "cpoe" in p.columns:
        cols.append("cpoe")
    recs = p[cols].rename(columns={"passer_player_id": "passer"}).to_dict("records")
    # team -> primary passer (most dropbacks) for slate mapping
    primary = (p.groupby("posteam")["passer_player_id"]
               .agg(lambda s: s.value_counts().idxmax()).to_dict())
    return recs, primary


def _qb_crude(df_before):
    p = df_before[(df_before.get("pass", 0) == 1)
                  & df_before["passer_player_name"].notna()
                  & df_before["epa"].notna()]
    out = {}
    for team, g in p.groupby("posteam"):
        top = g["passer_player_name"].value_counts().idxmax()
        out[team] = float(g[g["passer_player_name"] == top]["epa"].mean())
    return out


def build_rows(seasons):
    rows = []
    for yr, df in sorted(seasons.items()):
        if "result" not in df.columns:
            continue
        games = _reg_games(df)
        for w in sorted(int(x) for x in games["week"].unique() if x >= TRUST_WEEK):
            before = df[df["week"] < w]
            gb = before[before.get("season_type", "REG") == "REG"]
            if gb.empty:
                continue
            pr = _points_ratings(gb)
            crude = _qb_crude(gb)
            recs, primary = _pass_plays(gb)
            pr_qb = epa.passer_epa_ratings(recs, lam=LAM_QB)
            for _, g in games[games["week"] == w].iterrows():
                h, a, actual = g["home"], g["away"], float(g["result"])
                if not (h in pr and a in pr):
                    continue
                gid = g["game_id"]
                pm = (pr[h][0] + pr[a][1]) / 2 - (pr[a][0] + pr[h][1]) / 2 + HFA
                qc = crude.get(h, 0.0) - crude.get(a, 0.0)
                hq, aq = primary.get(h), primary.get(a)
                adj = ((pr_qb[hq].epa if hq in pr_qb else 0.0)
                       - (pr_qb[aq].epa if aq in pr_qb else 0.0))
                cp = ((pr_qb[hq].cpoe if hq in pr_qb else 0.0)
                      - (pr_qb[aq].cpoe if aq in pr_qb else 0.0))
                rows.append({"pm": pm, "qc": qc, "adj": adj, "cp": cp,
                             "margin": actual, "y": 1.0 if actual > 0 else 0.0,
                             "game_id": gid, "season": yr, "week": w})
    return rows


def _fit_predict(X, y, x_now):
    b = np.linalg.lstsq(np.array(X), np.array(y), rcond=None)[0]
    return float(np.dot(b, x_now)), b


def main():
    seasons = load_seasons()
    if not seasons:
        print("No PBP parquet under", PBP_DIR)
        return
    rows = build_rows(seasons)
    names = ("Points (raw)", "Stack(pts+QBcrude)", "Stack(pts+QBadj)",
             "Stack(pts+QBadj+cpoe)")
    m = {k: Metrics(k) for k in names}
    Xc, Xa, Xac, margins = [], [], [], []
    for r in rows:
        pm, qc, adj, cp, y = r["pm"], r["qc"], r["adj"], r["cp"], r["y"]
        m["Points (raw)"].add(_phi(pm / SIGMA), y)
        # learned stacks regress the actual point MARGIN (expanding window),
        # then map the predicted margin to a win prob via Phi(pred/sigma).
        if len(margins) >= MIN_TRAIN:
            pc, _ = _fit_predict(Xc, margins, [1.0, pm, qc])
            pa_, _ = _fit_predict(Xa, margins, [1.0, pm, adj])
            pac, _ = _fit_predict(Xac, margins, [1.0, pm, adj, cp])
        else:
            pc = pa_ = pac = pm
        m["Stack(pts+QBcrude)"].add(_phi(pc / SIGMA), y)
        m["Stack(pts+QBadj)"].add(_phi(pa_ / SIGMA), y)
        m["Stack(pts+QBadj+cpoe)"].add(_phi(pac / SIGMA), y)
        Xc.append([1.0, pm, qc]); Xa.append([1.0, pm, adj])
        Xac.append([1.0, pm, adj, cp]); margins.append(r["margin"])
    print(f"\nWalk-forward QB bake-off — seasons {sorted(seasons)}, "
          f"week>={TRUST_WEEK}, lam_qb={LAM_QB}\n")
    for k in names:
        print(m[k].row())
    if len(margins) >= MIN_TRAIN:
        _, ba = _fit_predict(Xa, margins, [1.0, 0, 0])
        _, bac = _fit_predict(Xac, margins, [1.0, 0, 0, 0])
        print(f"\nFinal coefs (margin):")
        print(f"  pts+QBadj:      points={ba[1]:.3f}, qb_adj={ba[2]:.3f}")
        print(f"  pts+QBadj+cpoe: points={bac[1]:.3f}, qb_adj={bac[2]:.3f}, "
              f"cpoe={bac[3]:.3f}")


if __name__ == "__main__":
    main()
