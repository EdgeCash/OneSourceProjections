"""Soccer match model: 1X2 / totals / BTTS from expected goals. Pure, offline."""
from __future__ import annotations

import pytest

from project547.models import soccer


def test_outcome_probs_sum_to_one():
    for h, a in [(1.6, 1.1), (1.45, 1.45), (0.9, 1.8), (3.5, 0.4)]:
        o = soccer.outcome_probs(h, a)
        assert sum(o.values()) == pytest.approx(1.0, abs=1e-3)
        assert all(0.0 <= v <= 1.0 for v in o.values())


def test_symmetric_match_is_balanced():
    o = soccer.outcome_probs(1.45, 1.45)
    assert o["home"] == pytest.approx(o["away"], abs=1e-6)   # no home edge baked in
    assert 0.22 < o["draw"] < 0.32                            # realistic draw rate


def test_stronger_side_favoured():
    o = soccer.outcome_probs(2.1, 0.9)
    assert o["home"] > o["away"]
    weak = soccer.outcome_probs(0.9, 2.1)
    assert weak["away"] > weak["home"]


def test_dixon_coles_inflates_draws_vs_independent():
    # rho<0 should lift the draw probability above the independent-Poisson value
    dc = soccer.outcome_probs(1.3, 1.2, rho=soccer.RHO)["draw"]
    indep = soccer.outcome_probs(1.3, 1.2, rho=0.0)["draw"]
    assert dc > indep


def test_over_prob_monotonic_in_goals_and_line():
    assert soccer.over_prob(2.0, 2.0, 2.5) > soccer.over_prob(0.8, 0.8, 2.5)
    assert soccer.over_prob(1.5, 1.5, 1.5) > soccer.over_prob(1.5, 1.5, 3.5)


def test_btts_rises_with_both_expectations():
    assert soccer.btts_prob(1.6, 1.6) > soccer.btts_prob(1.6, 0.3)
    assert 0.0 <= soccer.btts_prob(1.4, 1.4) <= 1.0
