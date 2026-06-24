"""Walk-forward validation: do opponent-adjusted EPA ratings project NFL games
better than the points-based ratings the generic model uses today?

For each season, for each game in week >= TRUST_WEEK, we build both rating
systems from *only that season's earlier weeks* (no leakage) and predict the
home margin / win probability, then score against the actual result with proper
scoring rules. This is the Stage-1 gate in docs/ACCURACY_ROADMAP.md: EPA only
gets wired into live projections if it wins here.

Run: python scripts/validate_epa.py
Requires nflverse play-by-play parquet under data/history/pbp/nfl/.
"""
from __future__ import annotations

import math
import pathlib

import pandas as pd

from onesource import epa

PBP_DIR = pathlib.Path("data/history/pbp/nfl")
TRUST_WEEK = 5          # skip early weeks where ratings are thin (research)
SIGMA = 13.5            # NFL margin sd for the win-prob map
HFA = 2.0
RIDGE_LAM = 25.0


def _phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _clip(p: float, eps: float = 1e-6) -> float:
    return min(max(p, eps), 1 - eps)


class Metrics:
    def __init__(self, name: str):
        self.name = name
        self.n = 0
        self.brier = 0.0
        self.logloss = 0.0
        self.margin_ae = 0.0
        self.win_correct = 0

    def add(self, p_home_win: float, pred_margin: float, actual_margin: float):
        y = 1.0 if actual_margin > 0 else 0.0
        p = _clip(p_home_win)
        self.n += 1
        self.brier += (p - y) ** 2
        self.logloss += -(y * math.log(p) + (1 - y) * math.log(1 - p))
        self.margin_ae += abs(pred_margin - actual_margin)
        if (p >= 0.5) == (y == 1.0):
            self.win_correct += 1

    def row(self) -> str:
        n = max(self.n, 1)
        return (f"{self.name:<14} n={self.n:<5} "
                f"Brier={self.brier/n:.4f}  LogLoss={self.logloss/n:.4f}  "
                f"MarginMAE={self.margin_ae/n:.2f}  Win%={100*self.win_correct/n:.1f}")


def load_seasons() -> dict[int, pd.DataFrame]:
    out = {}
    for f in sorted(PBP_DIR.glob("play_by_play_*.parquet")):
        try:
            df = pd.read_parquet(f)
        except Exception as e:  # truncated/corrupt download
            print(f"  skip {f.name}: {type(e).__name__}")
            continue
        yr = int(f.stem.split("_")[-1])
        out[yr] = df
    return out


def game_results(df: pd.DataFrame) -> pd.DataFrame:
    """One row per regular-season game: week, home, away, home final margin."""
    reg = df[df.get("season_type", "REG") == "REG"]
    g = reg.groupby("game_id").agg(
        week=("week", "first"), home=("home_team", "first"),
        away=("away_team", "first"), result=("result", "first")).reset_index()
    return g.dropna(subset=["result", "home", "away", "week"])


def points_ratings_from_scores(df_before: pd.DataFrame) -> dict[str, tuple[float, float]]:
    reg = df_before
    g = reg.groupby("game_id").agg(
        home=("home_team", "first"), away=("away_team", "first"),
        hs=("home_score", "first"), as_=("away_score", "first")).reset_index()
    pf, pa, n = {}, {}, {}
    for _, r in g.iterrows():
        for t, scored, allowed in [(r["home"], r["hs"], r["as_"]),
                                    (r["away"], r["as_"], r["hs"])]:
            pf[t] = pf.get(t, 0) + scored
            pa[t] = pa.get(t, 0) + allowed
            n[t] = n.get(t, 0) + 1
    return {t: (pf[t] / n[t], pa[t] / n[t]) for t in n}


def epa_plays(df_before: pd.DataFrame) -> list[dict]:
    d = df_before[df_before["play_type"].isin(("pass", "run"))]
    d = d[d["epa"].notna() & d["posteam"].notna() & d["defteam"].notna()]
    if "wp" in d.columns:
        d = d[(d["wp"] > 0.05) & (d["wp"] < 0.95)]
    cols = ["posteam", "defteam", "epa"] + (["success"] if "success" in d.columns else [])
    return d[cols].to_dict("records")


def main():
    seasons = load_seasons()
    if not seasons:
        print("No PBP parquet found under", PBP_DIR)
        return
    m_epa, m_pts, m_blend = Metrics("EPA"), Metrics("Points(base)"), Metrics("Blend 50/50")
    for yr, df in sorted(seasons.items()):
        if "result" not in df.columns or "home_score" not in df.columns:
            print(f"  {yr}: missing columns, skipping"); continue
        results = game_results(df)
        weeks = sorted(int(w) for w in results["week"].unique() if w >= TRUST_WEEK)
        for w in weeks:
            before = df[df["week"] < w]
            gb = before[before.get("season_type", "REG") == "REG"]
            if gb.empty:
                continue
            er = epa.team_epa_ratings(epa_plays(gb), league="nfl", lam=RIDGE_LAM)
            pr = points_ratings_from_scores(gb)
            wk_games = results[results["week"] == w]
            for _, g in wk_games.iterrows():
                h, a, actual = g["home"], g["away"], float(g["result"])
                if h in er and a in er:
                    em = epa.expected_margin(er[h], er[a], league="nfl", hfa=HFA)
                    m_epa.add(_phi(em / SIGMA), em, actual)
                else:
                    em = None
                if h in pr and a in pr:
                    h_exp = (pr[h][0] + pr[a][1]) / 2
                    a_exp = (pr[a][0] + pr[h][1]) / 2
                    pm = h_exp - a_exp + HFA
                    m_pts.add(_phi(pm / SIGMA), pm, actual)
                else:
                    pm = None
                if em is not None and pm is not None:
                    bm = 0.5 * em + 0.5 * pm
                    m_blend.add(_phi(bm / SIGMA), bm, actual)
    print("\nWalk-forward NFL validation (week >= %d), seasons %s\n" %
          (TRUST_WEEK, ", ".join(map(str, sorted(seasons)))))
    print(m_pts.row())
    print(m_epa.row())
    print(m_blend.row())
    print("\nLower Brier/LogLoss/MarginMAE = better; higher Win% = better.")


if __name__ == "__main__":
    main()
