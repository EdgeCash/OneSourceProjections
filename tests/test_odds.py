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


def test_fair_two_way_rejects_incoherent_prices():
    # a coherent two-way (-110 / -110) devigs to 50/50
    fair = odds.fair_two_way(-110, -110)
    assert fair and math.isclose(fair[0], 0.5) and math.isclose(fair[1], 0.5)
    # the real bug case: over +104 paired with a stale under +1349 sums to
    # ~0.56 of implied prob -> rejected so it can't fake an edge
    assert odds.fair_two_way(+104, +1349) is None
    # a heavily-vigged but coherent market still passes and favors the fav
    fair = odds.fair_two_way(-200, +170)
    assert fair is not None and fair[0] > fair[1]


def test_fair_one_way_bounds():
    assert odds.fair_one_way(-110) is not None
    # an absurd long-shot price (implied < 1%) is rejected
    assert odds.fair_one_way(+12000) is None


def test_blend_toward_market():
    # shrink 0 keeps the model, 1 trusts the market, 0.5 splits the gap
    assert odds.blend_toward_market(0.70, 0.50, 0.0) == 0.70
    assert odds.blend_toward_market(0.70, 0.50, 1.0) == 0.50
    assert math.isclose(odds.blend_toward_market(0.70, 0.50, 0.5), 0.60)
