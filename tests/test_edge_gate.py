"""Per-market demonstrated-edge gate."""
from project547 import config, edge_gate


def _led(sport, market, n, clv, pnl=0.0, won=True):
    return [{"sport": sport, "market": market, "date": "2026-06-20", "pnl": pnl,
             "clv": clv, "won": won} for _ in range(n)]


def test_classify_cleared_on_positive_clv_with_sample():
    stat = {"clv_n": config.GATE_CLEAR_MIN, "avg_clv": 0.02}
    assert edge_gate.classify(stat) == edge_gate.CLEARED


def test_classify_probation_when_thin_sample():
    stat = {"clv_n": config.GATE_CLEAR_MIN - 1, "avg_clv": 0.05}
    assert edge_gate.classify(stat) == edge_gate.PROBATION


def test_classify_gated_only_on_strong_negative():
    # small negative sample -> still probation (don't kill on noise)
    assert edge_gate.classify({"clv_n": config.GATE_CLEAR_MIN,
                               "avg_clv": -0.05}) == edge_gate.PROBATION
    # enough negative evidence -> gated
    assert edge_gate.classify({"clv_n": config.GATE_OFF_MIN,
                               "avg_clv": -0.05}) == edge_gate.GATED


def test_classify_none_is_probation():
    assert edge_gate.classify(None) == edge_gate.PROBATION
    assert edge_gate.classify({"clv_n": 100, "avg_clv": None}) == edge_gate.PROBATION


def test_market_stats_and_gate_table():
    led = (_led("MLB", "moneyline", config.GATE_CLEAR_MIN + 5, 0.04, pnl=0.1)
           + _led("MLB", "total", config.GATE_OFF_MIN + 5, -0.05, pnl=-0.2, won=False))
    table = edge_gate.gate_table(ledger=led, asof="2026-06-25")
    assert table[("MLB", "moneyline")]["status"] == edge_gate.CLEARED
    assert table[("MLB", "total")]["status"] == edge_gate.GATED
    # unknown market defaults to probation
    assert edge_gate.status_for("NHL", "moneyline", table) == edge_gate.PROBATION


def test_cap_tier_effects():
    assert edge_gate.cap_tier("core", edge_gate.GATED) == "pass"
    assert edge_gate.cap_tier("core", edge_gate.PROBATION) == "lean"
    assert edge_gate.cap_tier("core", edge_gate.CLEARED) == "core"
    # verify (a warning) survives any gate
    assert edge_gate.cap_tier("verify", edge_gate.GATED) == "verify"


def test_stake_and_curatable():
    assert edge_gate.stake_mult(edge_gate.CLEARED) == 1.0
    assert edge_gate.stake_mult(edge_gate.GATED) == 0.0
    assert 0 < edge_gate.stake_mult(edge_gate.PROBATION) < 1.0
    assert edge_gate.is_curatable(edge_gate.CLEARED)
    assert not edge_gate.is_curatable(edge_gate.GATED)


def test_window_excludes_old_bets():
    old = [{"sport": "MLB", "market": "moneyline", "date": "2020-01-01",
            "pnl": 0.1, "clv": 0.05, "won": True}]
    stats = edge_gate.market_stats(ledger=old, asof="2026-06-25", window_days=180)
    assert ("MLB", "moneyline") not in stats
