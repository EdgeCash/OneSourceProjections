"""Odds math: conversions, vig removal, expected value, Kelly sizing."""

from __future__ import annotations


def american_to_decimal(odds: float) -> float:
    if odds > 0:
        return 1 + odds / 100.0
    return 1 + 100.0 / abs(odds)


def decimal_to_american(dec: float) -> int:
    if dec >= 2:
        return round((dec - 1) * 100)
    return round(-100 / (dec - 1))


def implied_prob(american: float) -> float:
    """Raw implied probability including the book's vig."""
    if american > 0:
        return 100.0 / (american + 100.0)
    return abs(american) / (abs(american) + 100.0)


def devig_two_way(p_a: float, p_b: float) -> tuple[float, float]:
    """Multiplicative (proportional) vig removal for a two-way market."""
    total = p_a + p_b
    if total <= 0:
        raise ValueError("implied probabilities must be positive")
    return p_a / total, p_b / total


def expected_value(model_prob: float, american: float) -> float:
    """EV per 1 unit staked at the quoted price given our win probability."""
    dec = american_to_decimal(american)
    return model_prob * (dec - 1) - (1 - model_prob)


def kelly_stake(model_prob: float, american: float, fraction: float = 1.0) -> float:
    """Kelly criterion stake as a fraction of bankroll (0 if no edge)."""
    b = american_to_decimal(american) - 1
    if b <= 0:
        return 0.0
    full = (model_prob * b - (1 - model_prob)) / b
    return max(0.0, full * fraction)


def fair_two_way(a_american: float, b_american: float,
                 vig_min: float = 0.98, vig_max: float = 1.30
                 ) -> tuple[float, float] | None:
    """De-vigged fair probabilities for the two sides of a market, or None
    when the price pair is incoherent.

    The two raw implied probabilities should sum to a little over 1.0 (the
    book's hold). If their sum falls outside ``[vig_min, vig_max]`` the
    quotes are stale, mismatched, or for different lines (e.g. an over at
    +104 paired with an under at +1349, which sum to ~0.56) — we reject the
    pair so it can't manufacture a phantom edge.
    """
    try:
        pa, pb = implied_prob(a_american), implied_prob(b_american)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    total = pa + pb
    if not (vig_min <= total <= vig_max):
        return None
    return pa / total, pb / total


def fair_one_way(american: float, lo: float = 0.01, hi: float = 0.99
                 ) -> float | None:
    """Implied probability of a single quoted side, or None if the price is
    implausible (implied prob outside ``[lo, hi]``). Used as a conservative,
    vig-inclusive market anchor when only one side of a market is priced."""
    try:
        p = implied_prob(american)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return p if lo <= p <= hi else None


def blend_toward_market(model_prob: float, market_fair: float,
                        shrink: float) -> float:
    """Shrink a model probability toward the market's fair probability.
    ``shrink`` in [0, 1]; 0 keeps the model untouched, 1 trusts the market
    fully. Backtests show un-shrunk model edges are largely noise."""
    return (1.0 - shrink) * model_prob + shrink * market_fair
