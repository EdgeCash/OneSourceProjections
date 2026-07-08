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
    assert edge_gate.status_for("WNBA", "total", table) == edge_gate.PROBATION
    # backtest-proven losers are held (GATED) regardless of live history
    assert edge_gate.status_for("NHL", "moneyline", table) == edge_gate.GATED


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


# ---------------------------------------------------------------------------
# audit #21: variance-aware CLEAR, EV-band restriction, held totals
# ---------------------------------------------------------------------------

def test_marginal_high_variance_market_stays_probation():
    """avg CLV barely positive but noisy: the lower confidence bound
    (avg - 1.28*SE) sits below the floor -> PROBATION, not CLEARED."""
    n = config.GATE_CLEAR_MIN + 10
    # alternate +5% / -4.8% CLV: avg = +0.1%, sd ~ 4.9% -> lb << 0
    led = []
    for i in range(n):
        led += _led("MLB", "moneyline", 1, 0.05 if i % 2 == 0 else -0.048)
    table = edge_gate.gate_table(ledger=led, asof="2026-06-25")
    stat = table[("MLB", "moneyline")]
    assert stat["avg_clv"] > 0
    assert stat["clv_lb"] < config.GATE_CLV_FLOOR
    assert stat["status"] == edge_gate.PROBATION


def test_consistent_positive_clv_still_clears():
    led = _led("MLB", "moneyline", config.GATE_CLEAR_MIN + 5, 0.03)
    table = edge_gate.gate_table(ledger=led, asof="2026-06-25")
    assert table[("MLB", "moneyline")]["status"] == edge_gate.CLEARED


def test_classify_without_variance_info_falls_back_to_point_estimate():
    # legacy stats dict (no clv_lb): behaves like the old point-estimate rule
    stat = {"clv_n": config.GATE_CLEAR_MIN, "avg_clv": 0.02}
    assert edge_gate.classify(stat) == edge_gate.CLEARED


def test_verify_band_rows_excluded_from_gate_stats():
    """>= STALE_EV 'verify' bets (stale lines, never curated) must not be able
    to gate a market off — or clear it."""
    good = _led("MLB", "total", config.GATE_CLEAR_MIN + 5, 0.02)
    stale = [dict(r, ev=config.STALE_EV + 0.02, clv=-0.20)
             for r in _led("MLB", "total", 200, -0.20)]
    with_band = edge_gate.market_stats(ledger=good + stale, asof="2026-06-25")
    stat = with_band[("MLB", "total")]
    assert stat["clv_n"] == config.GATE_CLEAR_MIN + 5   # stale rows filtered
    assert stat["avg_clv"] == 0.02
    assert edge_gate.classify(stat) == edge_gate.CLEARED


def test_in_band_rows_kept():
    rows = [dict(r, ev=0.04) for r in _led("MLB", "total", 10, 0.02)]
    stats = edge_gate.market_stats(ledger=rows, asof="2026-06-25")
    assert stats[("MLB", "total")]["n"] == 10


def test_condemned_totals_are_held():
    # T0.1 condemned NBA/NFL/NHL totals as hard as their moneylines
    for sport in ("NBA", "NFL", "NHL"):
        assert edge_gate.status_for(sport, "total", {}) == edge_gate.GATED
        assert edge_gate.status_for(sport, "moneyline", {}) == edge_gate.GATED
    # other totals unaffected
    assert edge_gate.status_for("MLB", "total", {}) == edge_gate.PROBATION


# --- conviction (rank by proven edge, not raw EV) — Component 1 --------------

def test_ev_in_band_uses_global_sharp_band():
    assert edge_gate.ev_in_band(config.SHARP_EV_MIN, "MLB", "moneyline")
    assert edge_gate.ev_in_band(config.SHARP_EV_MAX, "MLB", "moneyline")
    assert not edge_gate.ev_in_band(config.SHARP_EV_MIN - 0.001, "MLB", "moneyline")
    assert not edge_gate.ev_in_band(config.STALE_EV, "MLB", "moneyline")
    assert not edge_gate.ev_in_band(None, "MLB", "moneyline")


def test_conviction_zero_without_history_or_out_of_band():
    stat = {"clv_n": config.GATE_CLEAR_MIN, "avg_clv": 0.03, "clv_lb": 0.02}
    ev = (config.SHARP_EV_MIN + config.SHARP_EV_MAX) / 2
    assert edge_gate.conviction(ev, "MLB", "moneyline", None) == 0.0      # no history
    assert edge_gate.conviction(config.STALE_EV, "MLB", "moneyline", stat) == 0.0  # out of band


def test_conviction_zero_when_not_beating_close():
    # in-band EV but the market's lower bound isn't positive -> no conviction
    stat = {"clv_n": config.GATE_CLEAR_MIN, "avg_clv": 0.01, "clv_lb": -0.005}
    ev = (config.SHARP_EV_MIN + config.SHARP_EV_MAX) / 2
    assert edge_gate.conviction(ev, "MLB", "moneyline", stat) == 0.0


def test_conviction_scales_with_lb_and_sample():
    ev = (config.SHARP_EV_MIN + config.SHARP_EV_MAX) / 2
    full = {"clv_n": config.GATE_CLEAR_MIN, "avg_clv": 0.04, "clv_lb": 0.02}
    assert edge_gate.conviction(ev, "MLB", "moneyline", full) == 0.02
    # half the clearing sample -> half conviction (confidence discount)
    half = {"clv_n": config.GATE_CLEAR_MIN // 2, "avg_clv": 0.04, "clv_lb": 0.02}
    assert edge_gate.conviction(ev, "MLB", "moneyline", half) == 0.01
    # a stronger proven edge outranks a weaker one at the same EV
    strong = {"clv_n": config.GATE_CLEAR_MIN, "avg_clv": 0.06, "clv_lb": 0.05}
    assert (edge_gate.conviction(ev, "MLB", "moneyline", strong)
            > edge_gate.conviction(ev, "MLB", "moneyline", full))


def test_conviction_legacy_stat_falls_back_to_point_estimate():
    ev = (config.SHARP_EV_MIN + config.SHARP_EV_MAX) / 2
    legacy = {"clv_n": config.GATE_CLEAR_MIN, "avg_clv": 0.03}   # no clv_lb
    assert edge_gate.conviction(ev, "MLB", "moneyline", legacy) == 0.03
