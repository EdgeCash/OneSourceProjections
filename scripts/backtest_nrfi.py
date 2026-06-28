"""Walk-forward backtest of the NRFI model vs the base-rate baseline.

Builds one row per MLB game from the team-game log, computes as-of (no-lookahead)
first-inning rates per date, scores P(YRFI) with project547.models.nrfi, and
reports Brier / log-loss / calibration vs a constant base-rate baseline.

    python scripts/backtest_nrfi.py [--seasons 2025,2026] [--min-date 2025-04-15]
"""

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from project547 import parks, teamstats  # noqa: E402
from project547.models import nrfi  # noqa: E402


def per_game_frame(df: pd.DataFrame) -> pd.DataFrame:
    """One row per game from the home-team rows of the team-game log."""
    home = df[df["is_home"] == True].copy()  # noqa: E712
    f1 = pd.to_numeric(home["f1"], errors="coerce")
    of1 = pd.to_numeric(home["opp_f1"], errors="coerce")
    home["yrfi"] = ((f1 > 0) | (of1 > 0)).astype(int)
    home = home.dropna(subset=["f1", "opp_f1"])
    return home[["date", "team", "opp", "season", "yrfi"]].rename(
        columns={"team": "home", "opp": "away"})


def _brier(p, y):
    return float(np.mean((np.asarray(p) - np.asarray(y)) ** 2))


def _logloss(p, y):
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    y = np.asarray(y, float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def backtest(seasons, min_date=None):
    df = teamstats.team_games("MLB", tuple(seasons))
    games = per_game_frame(df)
    if min_date:
        games = games[games["date"].astype(str) >= min_date]
    games = games.sort_values("date").reset_index(drop=True)

    # as-of rates per date (computed once per date from all prior games)
    preds, ys = [], []
    rate_cache: dict = {}
    for date, day in games.groupby("date"):
        if date not in rate_cache:
            rate_cache[date] = nrfi.team_first_inning_rates(df, asof=date)
        rates = rate_cache[date]
        for _, g in day.iterrows():
            h, a = g["home"], g["away"]
            rh, ra = rates.get(h), rates.get(a)
            if not rh or not ra:
                continue
            try:
                pf = parks.factor(h)
            except Exception:
                pf = 1.0
            r = nrfi.yrfi_prob(off_away=ra["off"], allow_home=rh["allow"],
                               off_home=rh["off"], allow_away=ra["allow"],
                               park=pf or 1.0)
            preds.append(r["p_yrfi"])
            ys.append(int(g["yrfi"]))

    preds, ys = np.array(preds), np.array(ys)
    n = len(ys)
    base = ys.mean()                       # constant-base-rate baseline
    out = {
        "n": n, "actual_yrfi": round(float(base), 4),
        "model_brier": round(_brier(preds, ys), 4),
        "base_brier": round(_brier(np.full(n, base), ys), 4),
        "model_logloss": round(_logloss(preds, ys), 4),
        "base_logloss": round(_logloss(np.full(n, base), ys), 4),
        "pred_mean": round(float(preds.mean()), 4),
        "pred_min": round(float(preds.min()), 4),
        "pred_max": round(float(preds.max()), 4),
    }
    # calibration deciles
    cal = []
    order = np.argsort(preds)
    for chunk in np.array_split(order, 10):
        if len(chunk) == 0:
            continue
        cal.append((round(float(preds[chunk].mean()), 3),
                    round(float(ys[chunk].mean()), 3), len(chunk)))
    return out, cal


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", default="2025,2026")
    ap.add_argument("--min-date", default=None,
                    help="skip early-season games with thin priors")
    args = ap.parse_args()
    seasons = [int(s) for s in args.seasons.split(",")]
    out, cal = backtest(seasons, args.min_date)
    print("=== NRFI backtest ===")
    for k, v in out.items():
        print(f"  {k}: {v}")
    print(f"  brier edge vs base: {round(out['base_brier'] - out['model_brier'], 4)} "
          f"(>0 = model better)")
    print("  calibration (pred -> actual, n):")
    for p, y, c in cal:
        print(f"    {p:.3f} -> {y:.3f}  (n={c})")
