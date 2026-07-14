"""Ensemble edge math — our own layer on top of BP/FP.

Combine four independent views of a prop into one calibrated probability and a
transparent 0-100 confidence score:

* ``model``  — our FantasyPros-driven per-stat distribution (Normal CDF).
* ``bp``     — BettingPros' own projection probability (premium field).
* ``form``   — the player's recent over-rate (BP performance window).
* ``market`` — the de-vigged sharp consensus probability.

Two principles keep us honest:

1. **Market anchoring.** The ensemble is shrunk toward the de-vigged market so a
   single overconfident source can't run away with a pick.
2. **Soft-line edge.** The real DFS edge is when the operator's line is off the
   sharp consensus line in our favor; that gap is scored explicitly.

Everything here is transparent and meant to be graded on closing-line value.
"""

from __future__ import annotations

from statistics import NormalDist

# Ensemble weights (before market anchoring). Tunable; model-led, market as a
# strong check, BP as a second model, recent form as a light prior.
WEIGHTS = {"model": 0.40, "bp": 0.30, "market": 0.20, "form": 0.10}
MARKET_ANCHOR = 0.35   # shrink the ensemble this far toward the market prob


def american_to_prob(odds) -> float | None:
    """Implied probability from American odds (no de-vig)."""
    try:
        o = float(odds)
    except (TypeError, ValueError):
        return None
    if o == 0:
        return None
    return (-o) / (-o + 100) if o < 0 else 100 / (o + 100)


def american_to_decimal(odds) -> float | None:
    """Decimal (payout multiplier incl. stake) from American odds."""
    try:
        o = float(odds)
    except (TypeError, ValueError):
        return None
    if o == 0:
        return None
    return 1 + (o / 100 if o > 0 else 100 / -o)


def devig_two_way(over_odds, under_odds) -> float | None:
    """Fair P(over) from a two-way price (proportional de-vig)."""
    po, pu = american_to_prob(over_odds), american_to_prob(under_odds)
    if po is None or pu is None or (po + pu) == 0:
        return None
    return po / (po + pu)


def model_over_prob(mean: float, std: float | None, line: float) -> float:
    """P(stat > line) under Normal(mean, std)."""
    if not std or std <= 0:
        return 1.0 if mean > line else 0.0
    return 1.0 - NormalDist(float(mean), float(std)).cdf(float(line))


def _blend(pairs) -> float | None:
    num = den = 0.0
    for p, w in pairs:
        if p is None:
            continue
        num += p * w
        den += w
    return (num / den) if den > 0 else None


def _clip(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def score(
    *,
    model_over: float,
    bp_over: float | None = None,
    form_over: float | None = None,
    market_over: float | None = None,
    dfs_line: float | None = None,
    consensus_line: float | None = None,
    break_even: float = 0.543,
    bet_rating: float | None = None,
) -> dict:
    """Fuse the signals into a calibrated pick with a confidence score.

    Returns the chosen side, calibrated win rate, edge vs break-even, edge vs the
    sharp market, the soft-line edge, cross-signal agreement, and a 0-100
    confidence composite.
    """
    ens = _blend([
        (model_over, WEIGHTS["model"]), (bp_over, WEIGHTS["bp"]),
        (market_over, WEIGHTS["market"]), (form_over, WEIGHTS["form"]),
    ])
    if ens is None:
        ens = model_over

    # Market anchoring: pull the ensemble toward the de-vigged market.
    p_over = ((1 - MARKET_ANCHOR) * ens + MARKET_ANCHOR * market_over
              if market_over is not None else ens)

    side = "OVER" if p_over >= 0.5 else "UNDER"
    win = p_over if side == "OVER" else 1 - p_over

    mkt_side = (None if market_over is None
                else (market_over if side == "OVER" else 1 - market_over))
    edge_market = None if mkt_side is None else round(win - mkt_side, 4)
    edge_be = round(win - break_even, 4)

    # Soft-line edge: is the DFS line off the sharp line in our favour?
    line_edge = None
    if dfs_line is not None and consensus_line is not None:
        line_edge = round((consensus_line - dfs_line) if side == "OVER"
                          else (dfs_line - consensus_line), 2)

    # Cross-signal agreement (how many independent views back our side).
    def favors(p):
        return None if p is None else ((p >= 0.5) == (side == "OVER"))

    present = [s for s in (favors(model_over), favors(bp_over),
                           favors(form_over), favors(market_over)) if s is not None]
    agreement = sum(1 for s in present if s)
    agree_frac = agreement / len(present) if present else 0.0

    # Confidence composite (0-100), transparent and weighted. A ~6% edge vs the
    # market, a full point of soft line, and unanimous signals ≈ elite (80s).
    edge_c = _clip((edge_market if edge_market is not None else edge_be) / 0.06)
    line_c = _clip((line_edge or 0) / 1.0)
    rating_c = (bet_rating / 5.0) if bet_rating else 0.5
    confidence = round(100 * (0.40 * edge_c + 0.25 * agree_frac
                              + 0.20 * line_c + 0.15 * rating_c))

    return {
        "side": side,
        "win_rate": round(win, 4),
        "p_over": round(p_over, 4),
        "ensemble": None if ens is None else round(ens, 4),
        "market_over": None if market_over is None else round(market_over, 4),
        "edge_vs_breakeven": edge_be,
        "edge_vs_market": edge_market,
        "line_edge": line_edge,
        "agreement": agreement,
        "n_signals": len(present),
        "confidence": confidence,
    }
