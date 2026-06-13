import pytest

from onesource import sgp


def test_two_leg_independent_matches_product():
    r = sgp.price_sgp([0.5, 0.5], rho=0.0)
    assert r["joint_prob"] == 0.25
    assert r["independent_prob"] == 0.25
    assert r["lift"] == 0.0
    assert r["fair_american"] == 300  # decimal 4.0 -> +300


def test_positive_correlation_raises_joint_and_shortens_price():
    indep = sgp.price_sgp([0.5, 0.5], rho=0.0)
    corr = sgp.price_sgp([0.5, 0.5], rho=0.4)
    assert corr["joint_prob"] > indep["joint_prob"]
    assert corr["lift"] > 0
    # more likely to hit together -> fair price is shorter (smaller payout)
    assert corr["fair_american"] < indep["fair_american"]


def test_negative_correlation_lowers_joint():
    corr = sgp.price_sgp([0.5, 0.5], rho=-0.3)
    assert corr["joint_prob"] < 0.25
    assert corr["lift"] < 0


def test_quoted_price_ev_positive_when_book_prices_as_independent():
    # legs are positively correlated, but the book quotes the independent price
    indep_price = sgp.price_sgp([0.6, 0.6], rho=0.0)["fair_american"]
    r = sgp.price_sgp([0.6, 0.6], rho=0.35, quoted_american=indep_price)
    # true joint > independent implied -> taking the independent quote is +EV
    assert r["ev"] > 0
    assert r["stake"] > 0
    assert 0 < r["quoted_prob"] < 1


def test_three_leg_uses_copula_and_lifts_with_correlation():
    indep = sgp.price_sgp([0.5, 0.5, 0.5], rho=0.0)
    assert abs(indep["joint_prob"] - 0.125) < 0.01
    corr = sgp.price_sgp([0.5, 0.5, 0.5], rho=0.4)
    assert corr["joint_prob"] > indep["joint_prob"]


def test_validates_inputs():
    with pytest.raises(ValueError):
        sgp.price_sgp([0.5])               # need >= 2 legs
    with pytest.raises(ValueError):
        sgp.price_sgp([0.5, 1.0])          # prob out of (0,1)


def test_presets_have_independent_zero():
    assert sgp.CORRELATION_PRESETS["Unrelated / different games (independent)"] == 0.0
    assert any(v < 0 for v in sgp.CORRELATION_PRESETS.values())  # has negatives
    assert any(v > 0 for v in sgp.CORRELATION_PRESETS.values())
