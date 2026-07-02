"""WNBA prop rate model: NB distribution, recency-weighted shrunk rate, and
market vocabulary. All offline / pure."""
from __future__ import annotations

import pytest

from project547.models import wnba_props as wp


def test_nb_cdf_is_a_distribution():
    # sums to ~1 over a wide support and is monotone non-decreasing
    prev = -1.0
    for k in range(0, 80):
        c = wp._nb_cdf(k, mean=9.0, r=2.4)
        assert c >= prev - 1e-12
        prev = c
    assert wp._nb_cdf(200, mean=9.0, r=2.4) == pytest.approx(1.0, abs=1e-3)


def test_prob_over_monotonic_in_line():
    ps = [wp.prob_over(9.0, line, r=2.4) for line in (4.5, 8.5, 12.5, 20.5)]
    assert ps == sorted(ps, reverse=True)          # higher line -> lower P(over)
    assert 0.0 < ps[1] < 1.0


def test_prob_over_rises_with_mean():
    lo = wp.prob_over(6.0, 9.5, r=2.4)
    hi = wp.prob_over(14.0, 9.5, r=2.4)
    assert hi > lo


def test_nb_mean_matches_target():
    # E[X] under the iterative pmf should recover the requested mean
    mean, r = 8.0, 3.7
    p = r / (r + mean)
    pmf = p ** r
    ex = 0.0
    for k in range(1, 400):
        pmf *= (k - 1 + r) / k * (1 - p)
        ex += k * pmf
    assert ex == pytest.approx(mean, rel=1e-3)


def test_weighted_rate_favors_recent_games():
    # trailing hot streak should pull the rate above the simple mean
    cold_then_hot = [2, 2, 2, 2, 20, 20, 20, 20]
    rate, _ = wp.weighted_rate(cold_then_hot, base=8.5, prior=0.0)
    assert rate > sum(cold_then_hot) / len(cold_then_hot)


def test_weighted_rate_shrinks_thin_samples_to_base():
    # one huge game with strong prior shouldn't run away from the baseline
    rate, _ = wp.weighted_rate([40.0], base=8.5, prior=5.0)
    assert 8.5 < rate < 16.0


def test_project_and_markets():
    proj = wp.project([10, 12, 8, 14, 9, 11, 13], "points")
    assert proj.market == "points"
    assert proj.n == 7
    assert proj.r == wp.MARKETS["points"]["r"]
    assert wp.project([1, 2], "unknown market") is None


def test_market_aliases():
    assert wp.canonical_market("3-pointers made") == "threes"
    assert wp.canonical_market("PTS+REB+AST") == "pra"
    assert wp.canonical_market("Player Points") == "points"
    assert wp.canonical_market("") is None


def _defense_rows():
    # league is 10 pts/appearance; TOUGH allows 6, SOFT allows 14, with enough
    # appearances to clear the shrink prior and show a real (clamped) factor.
    rows = []
    for _ in range(200):
        rows.append({"team": "TOUGH", "points": 6.0})
        rows.append({"team": "SOFT", "points": 14.0})
        rows.append({"team": "AVG", "points": 10.0})
    return rows


def test_defense_factors_direction_and_clamp():
    tbl = wp.defense_factors(_defense_rows())
    tough = wp.opponent_factor("Points", "TOUGH", tbl)
    soft = wp.opponent_factor("Points", "SOFT", tbl)
    avg = wp.opponent_factor("Points", "AVG", tbl)
    assert tough < avg < soft                       # direction
    assert avg == pytest.approx(1.0, abs=1e-3)      # league-average team ~neutral
    # hard clamp at ±DEF_CLAMP even though raw factors are 0.6 / 1.4
    assert tough >= 1 - wp.DEF_CLAMP - 1e-9
    assert soft <= 1 + wp.DEF_CLAMP + 1e-9


def test_opponent_factor_neutral_when_unknown():
    tbl = wp.defense_factors(_defense_rows())
    assert wp.opponent_factor("Points", "NOPE", tbl) == 1.0      # unknown team
    assert wp.opponent_factor("Points", None, tbl) == 1.0        # no opponent
    assert wp.opponent_factor("bad market", "SOFT", tbl) == 1.0  # unknown market
    assert wp.opponent_factor("Points", "SOFT", {}) == 1.0       # empty table
