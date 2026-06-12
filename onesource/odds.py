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
