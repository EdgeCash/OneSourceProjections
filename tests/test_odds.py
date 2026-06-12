import math

from onesource import odds


def test_american_decimal_roundtrip():
    assert odds.american_to_decimal(+150) == 2.5
    assert odds.american_to_decimal(-150) == 1 + 100 / 150
    assert odds.decimal_to_american(2.5) == 150
    assert odds.decimal_to_american(1.5) == -200


def test_implied_prob():
    assert math.isclose(odds.implied_prob(-110), 110 / 210)
    assert math.isclose(odds.implied_prob(+100), 0.5)


def test_devig_two_way():
    p_a, p_b = odds.devig_two_way(odds.implied_prob(-110), odds.implied_prob(-110))
    assert math.isclose(p_a, 0.5) and math.isclose(p_b, 0.5)


def test_expected_value_and_kelly():
    # 55% to win at even money: EV = 0.10, full Kelly = 0.10
    assert math.isclose(odds.expected_value(0.55, +100), 0.10)
    assert math.isclose(odds.kelly_stake(0.55, +100), 0.10)
    assert math.isclose(odds.kelly_stake(0.55, +100, fraction=0.25), 0.025)
    # no edge -> no stake
    assert odds.kelly_stake(0.45, +100) == 0.0
