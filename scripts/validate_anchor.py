"""Validate the T3.1 market-anchored projection: sweep the anchor weight per
(sport, market) and report how much blending the published number toward the
market improves accuracy vs outcomes.

Method: run the production-mode walk-forward backtest with detail rows, which
carry both our model number and the de-vigged market number per game, plus the
outcome. For each anchor weight alpha we score the blended published number:
  - moneyline: Brier of  (1-alpha)*model_wp + alpha*market_fair  vs home_won
  - total:     MAE  of   (1-alpha)*proj_total + alpha*close_line vs actual_total

IMPORTANT CAVEAT (roadmap T3.1): the only market line in the backfill is the
CLOSING line, so this sweep anchors toward the *close* and is therefore an UPPER
BOUND on the accuracy gain — in production we anchor toward the current/open
line, which is softer. Per T0.1 the close-optimal alpha is ~1.0 for almost every
market, but a published number that equals the market has zero deviation -> zero
edge. So the recommendation is deliberately conservative: take a fraction of the
gain, keep alpha < 1 so the model's deviation survives for edge detection, and
set config.PROJECTION_ANCHOR from these numbers by hand after review.

Usage:
    python scripts/validate_anchor.py
    python scripts/validate_anchor.py --sports NBA,NFL,NHL,MLB --draws 2000
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from project547 import backtest  # noqa: E402

DEFAULT_SEASONS = {
    "MLB": [2024, 2025, 2026],
    "NBA": [2022, 2023, 2024, 2025],
    "NFL": [2021, 2022, 2023, 2024],
    "NHL": [2022, 2023, 2024, 2025],
    "WNBA": [2023, 2024, 2025],
}
GRID = [0.0, 0.25, 0.5, 0.75, 1.0]


def _ml_rows(detail):
    for d in detail:
        hwp, bias, won = d.get("home_win_prob"), d.get("home_prob_bias"), d.get("home_won")
        if hwp is None or bias is None or won is None:
            continue
        yield float(hwp), float(hwp) - float(bias), int(won)   # model, market_fair, outcome


def _tot_rows(detail):
    for d in detail:
        pt, line = d.get("proj_total"), d.get("close_total")
        hs, as_ = d.get("home_score"), d.get("away_score")
        if pt is None or line is None or hs is None or as_ is None:
            continue
        yield float(pt), float(line), float(hs) + float(as_)   # model, market_line, actual


def _brier_sweep(rows):
    rows = list(rows)
    if not rows:
        return None, {}
    out = {}
    for a in GRID:
        s = sum(((1 - a) * m + a * k - y) ** 2 for m, k, y in rows) / len(rows)
        out[a] = round(s, 4)
    return len(rows), out


def _mae_sweep(rows):
    rows = list(rows)
    if not rows:
        return None, {}
    out = {}
    for a in GRID:
        s = sum(abs((1 - a) * m + a * k - y) for m, k, y in rows) / len(rows)
        out[a] = round(s, 3)
    return len(rows), out


def _reco(sweep, endpoint_better_is_lower=True):
    """Conservative anchor: the smallest alpha capturing >= 50% of the 0->1 gain
    (never 1.0), so accuracy improves while the model keeps deviation for edges."""
    if not sweep:
        return None
    base, full = sweep[0.0], sweep[1.0]
    if full >= base:                       # market doesn't help -> no anchor
        return 0.0
    target = base - 0.5 * (base - full)
    for a in GRID:
        if a > 0 and sweep[a] <= target:
            return min(a, 0.75)
    return 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sports", default=",".join(DEFAULT_SEASONS))
    ap.add_argument("--draws", type=int, default=2000)
    args = ap.parse_args()
    sports = [s.strip().upper() for s in args.sports.split(",") if s.strip()]

    print(f"Anchor-weight sweep (grid {GRID}); alpha=0 model, alpha=1 market.\n"
          "Market proxy = CLOSING line => accuracy gain is an UPPER BOUND "
          "(production anchors to the softer current line).\n")
    reco = {}
    for sk in sports:
        seasons = DEFAULT_SEASONS.get(sk)
        if not seasons:
            continue
        print(f"=== {sk} {seasons} ===")
        try:
            res = backtest.run_game_backtest(
                sk, seasons, draws=args.draws, production_mode=True, detail=True,
                use_starters=(sk == "MLB"), use_bullpen=(sk == "MLB"),
                use_park=(sk == "MLB"))
        except Exception as e:
            print(f"  backtest failed: {e}\n")
            continue
        detail = res.get("games", [])
        n_ml, ml = _brier_sweep(_ml_rows(detail))
        n_tot, tot = _mae_sweep(_tot_rows(detail))
        if ml:
            r = _reco(ml)
            reco[(sk, "moneyline")] = r
            print(f"  moneyline Brier (n={n_ml}): "
                  + "  ".join(f"a={a}:{ml[a]}" for a in GRID)
                  + f"   -> reco alpha {r}")
        if tot:
            r = _reco(tot)
            reco[(sk, "total")] = r
            print(f"  total MAE  (n={n_tot}): "
                  + "  ".join(f"a={a}:{tot[a]}" for a in GRID)
                  + f"   -> reco alpha {r}")
        print()

    print("Suggested config.PROJECTION_ANCHOR (conservative; review before enabling):")
    print("PROJECTION_ANCHOR = {")
    for (sk, mkt), a in sorted(reco.items()):
        if a and a > 0:
            print(f'    ("{sk}", "{mkt}"): {a},')
    print("}")


if __name__ == "__main__":
    main()
