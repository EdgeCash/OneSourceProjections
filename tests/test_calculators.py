import math

from onesource import calculators as calc


def test_hold_and_no_vig():
    # -110/-110 market holds ~4.76%
    assert abs(calc.hold(-110, -110) - 0.0476) < 1e-3
    fair = calc.no_vig(-110, -110)
    assert abs(fair[0] - 0.5) < 1e-9 and abs(sum(fair) - 1) < 1e-9
    # power method also sums to 1 and favors the favorite
    fav = calc.no_vig(-200, +170, method="power")
    assert abs(sum(fav) - 1) < 1e-6 and fav[0] > fav[1]


def test_arbitrage_detects_and_sizes():
    # +110 / +110 is a clear arb (implied 0.476 each, sum < 1)
    arb = calc.arbitrage([+110, +110], total=100)
    assert arb is not None and arb["profit"] > 0
    assert abs(sum(arb["stakes"]) - 100) < 0.05
    # a normal vigged market is not an arb
    assert calc.arbitrage([-110, -110]) is None


def test_hedge_locks_equal_profit():
    # bet $100 at +200 (returns $300); hedge the other side at -150
    h = calc.hedge(100, +200, -150)
    # guaranteed profit identical whichever side wins
    assert h["hedge_stake"] > 0
    orig_win = 100 * 3 - h["total_outlay"]
    hedge_win = h["hedge_stake"] * (1 + 100 / 150) - h["total_outlay"]
    assert abs(orig_win - hedge_win) < 0.05
    assert abs(orig_win - h["guaranteed_profit"]) < 0.05


def test_middle_breakeven_small():
    # at -110/-110 a middle needs only a low single-digit hit rate
    be = calc.middle_breakeven(-110)
    assert 0.0 < be < 0.10


def test_risk_of_ruin_monotonic_in_fraction():
    # more aggressive staking (higher Kelly fraction) -> higher drawdown risk
    conservative = calc.risk_of_ruin(0.58, +100, fraction=0.25, sims=3000)
    aggressive = calc.risk_of_ruin(0.58, +100, fraction=1.0, sims=3000)
    assert 0.0 <= conservative <= aggressive <= 1.0
    # no edge -> certain ruin
    assert calc.risk_of_ruin(0.40, +100) == 1.0


def test_parlay_independent():
    p = calc.parlay([-110, -110])
    # two -110 legs ~ +264
    assert p["decimal"] > 3.6 and p["american"] > 250
    assert abs(p["implied_prob"] - (110 / 210) ** 2) < 1e-3


def test_correlated_two_leg():
    indep = calc.correlated_two_leg(0.5, 0.5, 0.0)
    assert abs(indep["joint_prob"] - 0.25) < 1e-9
    # positive correlation raises the joint probability
    pos = calc.correlated_two_leg(0.5, 0.5, 0.4)
    assert pos["joint_prob"] > 0.25
    assert math.isclose(pos["joint_prob"], 0.25 + 0.4 * 0.25)


def test_correlated_parlay_copula():
    probs = [0.55, 0.6, 0.5]
    indep = 0.55 * 0.6 * 0.5
    # rho=0 returns the exact independent product
    zero = calc.correlated_parlay(probs, rho=0.0)
    assert abs(zero["joint_prob"] - indep) < 1e-9 and zero["lift"] == 0.0
    # positive correlation lifts the all-hit probability above independent
    pos = calc.correlated_parlay(probs, rho=0.5)
    assert pos["joint_prob"] > indep and pos["lift"] > 0
    # Monte-Carlo with rho=0 should land near the independent product
    mc0 = calc.correlated_parlay(probs, rho=0.01)
    assert abs(mc0["joint_prob"] - indep) < 0.02
    # invalid rho (below the PSD bound) falls back to independent
    bad = calc.correlated_parlay([0.5, 0.5, 0.5], rho=-0.9)
    assert bad["joint_prob"] == bad["independent_prob"]
