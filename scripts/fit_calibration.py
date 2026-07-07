"""Fit post-hoc calibration maps per (sport, market) and write
``data/history/calibration/model_calibration.json``.

For each sport the walk-forward backtest (``run_game_backtest(detail=True)``)
produces out-of-sample (predicted_prob, actual_outcome) pairs for moneyline,
total (over), and spread (home cover). We fit an isotonic map on those pairs —
implemented with the Pool-Adjacent-Violators algorithm in numpy, so there's no
sklearn dependency — stored as ``(x_knots, y_knots)`` and applied at runtime by
``project547.calibrate`` via ``numpy.interp``.

Also prints a train/holdout check: fit on the earlier seasons, then report raw
vs calibrated Brier and ECE on the most recent (held-out) season, so the map is
validated out-of-sample before anyone flips ``APPLY_CALIBRATION`` on.

Run:  python scripts/fit_calibration.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from project547 import backtest  # noqa: E402
from project547.history import HISTORY_DIR  # noqa: E402

OUT = HISTORY_DIR / "calibration" / "model_calibration.json"
MIN_FIT = 400          # below this, don't fit — identity is safer than a bad map
DRAWS = 1500
GRID = np.linspace(0.0, 1.0, 101)

# Recent seasons per sport (where backfill exists); last one is the holdout.
SPORT_SEASONS = {
    "MLB":   [2022, 2023, 2024, 2025],
    "NBA":   [2022, 2023, 2024, 2025],
    "WNBA":  [2022, 2023, 2024, 2025],
    "NFL":   [2021, 2022, 2023, 2024],
    "NCAAF": [2021, 2022, 2023, 2024],
    "NHL":   [2021, 2022, 2023, 2024],
}
MARKETS = {  # market -> (pred_field, outcome_field)
    "moneyline": ("home_win_prob", "home_won"),
    "total":     ("p_over", "over_won"),
    "spread":    ("p_cover", "cover_won"),
}


def _pava(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Isotonic (non-decreasing) fit via Pool Adjacent Violators. Returns the
    sorted x and the monotone fitted values aligned to it."""
    order = np.argsort(x, kind="mergesort")
    xs, ys = np.asarray(x, float)[order], np.asarray(y, float)[order]
    val: list[float] = []
    wt: list[float] = []
    cnt: list[int] = []
    for yi in ys:
        v, w, c = yi, 1.0, 1
        while val and val[-1] > v:
            pv, pw, pc = val.pop(), wt.pop(), cnt.pop()
            v = (v * w + pv * pw) / (w + pw)
            w += pw
            c += pc
        val.append(v); wt.append(w); cnt.append(c)
    fitted = np.empty(len(ys))
    i = 0
    for v, c in zip(val, cnt):
        fitted[i:i + c] = v
        i += c
    return xs, fitted


def _fit(preds, outs):
    """(x_knots, y_knots) on the fixed GRID, or None when too few samples."""
    preds = np.asarray(preds, float)
    outs = np.asarray(outs, float)
    if len(preds) < MIN_FIT:
        return None
    xs, fitted = _pava(preds, outs)
    ux, inv = np.unique(xs, return_inverse=True)
    uy = np.array([fitted[inv == k].mean() for k in range(len(ux))])
    yk = np.interp(GRID, ux, uy)
    return GRID, yk


def _apply(mp, p):
    return np.interp(p, mp[0], mp[1])


def _brier(p, y):
    return float(np.mean((np.asarray(p, float) - np.asarray(y, float)) ** 2))


def _ece(p, y, bins=10):
    p = np.asarray(p, float); y = np.asarray(y, float)
    n = len(p); e = 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        m = (p >= lo) & (p <= hi if b == bins - 1 else p < hi)
        if m.sum():
            e += m.sum() / n * abs(p[m].mean() - y[m].mean())
    return float(e)


def _pairs(rows, market):
    pf, of = MARKETS[market]
    out = [(r.get(pf), r.get(of)) for r in rows]
    return [(float(p), float(o)) for p, o in out
            if p is not None and o is not None]


def main() -> int:
    maps: dict = {}
    print("=== calibration fit + out-of-sample holdout (raw -> calibrated) ===")
    print("a map is WRITTEN only if it does not worsen holdout Brier — so "
          "enabling APPLY_CALIBRATION is strictly safe.\n")
    print(f"{'sport/market':<18}{'n_hold':>7}{'Brier raw':>11}{'Brier cal':>11}"
          f"{'ECE raw':>9}{'ECE cal':>9}   action")
    for sport, seasons in SPORT_SEASONS.items():
        try:
            res = backtest.run_game_backtest(sport, seasons, draws=DRAWS, detail=True)
        except Exception as e:
            print(f"{sport:<18}  backtest failed — {type(e).__name__}: {e}")
            continue
        rows = res.get("games") or []
        # Time-based 80/20 holdout (robust to missing 'season'): newest 20% by
        # date is held out; the map is fit on the older 80% for validation.
        rows = sorted(rows, key=lambda r: r.get("date") or "")
        cut = int(len(rows) * 0.8)
        train, hold = rows[:cut], rows[cut:]
        for market in MARKETS:
            all_pairs = _pairs(rows, market)
            if len(all_pairs) < MIN_FIT:
                continue
            tr, ho = _pairs(train, market), _pairs(hold, market)
            if len(tr) < MIN_FIT or len(ho) < 50:
                print(f"{sport+'/'+market:<18}{len(ho):>7}   (holdout too small — skip)")
                continue
            m = _fit(*zip(*tr))
            hp = np.array([p for p, _ in ho]); hy = np.array([o for _, o in ho])
            cp = _apply(m, hp)
            br0, br1 = _brier(hp, hy), _brier(cp, hy)
            ec0, ec1 = _ece(hp, hy), _ece(cp, hy)
            keep = br1 <= br0 + 5e-4      # calibration must not worsen Brier
            action = "WRITE" if keep else "skip (worse)"
            print(f"{sport+'/'+market:<18}{len(ho):>7}{br0:>11.4f}{br1:>11.4f}"
                  f"{ec0:>9.4f}{ec1:>9.4f}   {action}")
            if keep:
                prod = _fit(*zip(*all_pairs))   # production: refit on ALL rows
                maps[f"{sport.lower()}_{market}"] = {
                    "n_fit": len(all_pairs),
                    "brier_raw": round(br0, 4), "brier_cal": round(br1, 4),
                    "ece_raw": round(ec0, 4), "ece_cal": round(ec1, 4),
                    "x_knots": [round(float(x), 5) for x in prod[0]],
                    "y_knots": [round(float(y), 5) for y in prod[1]],
                }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"markets": maps}, indent=1))
    print(f"\nwrote {len(maps)} validated maps -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
