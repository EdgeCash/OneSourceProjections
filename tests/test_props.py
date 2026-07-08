"""Push-probability helpers for the MLB prop distributions (audit #2/#3):
each P(over) helper that can price an integer line gets a matching pmf-at-line
helper so integer-line pushes stop being graded as losses (over) / wins (under).
"""

import math

from scipy import stats

from project547.models import props


def test_prob_push_count_matches_poisson_pmf():
    lam = 6.2
    push = props.prob_push_count(lam, 6)
    assert math.isclose(push, float(stats.poisson.pmf(6, lam)))
    assert push > 0.10                      # real mass at the mode
    # half lines can't push
    assert props.prob_push_count(lam, 6.5) == 0.0
    # over + push + under partition exactly at an integer line
    over = props.prob_over_count(lam, 6)
    under = float(stats.poisson.cdf(5, lam))
    assert math.isclose(over + push + under, 1.0)


def test_prob_push_neg_binom():
    mean, line = 1.8, 2
    push = props.prob_push_neg_binom(mean, line)
    p = props.TB_DISPERSION / (props.TB_DISPERSION + mean)
    assert math.isclose(push, float(stats.nbinom.pmf(line, props.TB_DISPERSION, p)))
    assert push > 0
    assert props.prob_push_neg_binom(mean, 1.5) == 0.0
    # custom dispersion mirrors prob_over_neg_binom's signature
    tight = props.prob_push_neg_binom(16.0, 16, dispersion=40.0)
    loose = props.prob_push_neg_binom(16.0, 16, dispersion=1.1)
    assert tight != loose
    # partition at an integer line, same dispersion both sides
    over = props.prob_over_neg_binom(mean, line)
    under = float(stats.nbinom.cdf(line - 1, props.TB_DISPERSION, p))
    assert math.isclose(over + push + under, 1.0)


def test_prob_push_hits():
    n_ab, p = 4.2, 0.28
    push = props.prob_push_hits(n_ab, p, 1)
    n = max(1, round(n_ab))
    a = props.HITS_CONCENTRATION * p
    b = props.HITS_CONCENTRATION * (1 - p)
    assert math.isclose(push, float(stats.betabinom.pmf(1, n, a, b)))
    assert push > 0
    assert props.prob_push_hits(n_ab, p, 0.5) == 0.0
    # partition with the over helper at the same concentration
    over = props.prob_over_hits(n_ab, p, 1)
    under = float(stats.betabinom.cdf(0, n, a, b))
    assert math.isclose(over + push + under, 1.0)
    # concentration keyword mirrors prob_over_hits
    assert (props.prob_push_hits(n_ab, p, 1, concentration=200)
            != props.prob_push_hits(n_ab, p, 1))


def test_over_helpers_unchanged_on_half_lines():
    """The over helpers still price half lines exactly as before — the push
    helpers are additive API, not a behavior change."""
    assert math.isclose(props.prob_over_count(6.2, 5.5),
                        float(1 - stats.poisson.cdf(5, 6.2)))
    p = props.TB_DISPERSION / (props.TB_DISPERSION + 1.8)
    assert math.isclose(props.prob_over_neg_binom(1.8, 1.5),
                        float(1 - stats.nbinom.cdf(1, props.TB_DISPERSION, p)))
