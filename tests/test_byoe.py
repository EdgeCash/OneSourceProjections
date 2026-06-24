"""Tests for the Build Your Own Edge engine and the Ask-AI risk modes."""

from __future__ import annotations

import pytest

from onesource import ai_modes, byoe
from onesource.sports import SPORTS

NFL = SPORTS["NFL"]

# Two stats; team A is strong on both, D is weak — z-scores then composite.
STATS = {
    "A": {"off": 30.0, "def_allowed": 17.0},
    "B": {"off": 24.0, "def_allowed": 22.0},
    "C": {"off": 20.0, "def_allowed": 26.0},
    "D": {"off": 14.0, "def_allowed": 31.0},
}
# weight offense up, defense-allowed down (lower allowed = better)
EDGE = byoe.Edge(name="t", inputs=(byoe.EdgeInput("off", 1.0),
                                   byoe.EdgeInput("def_allowed", -1.0)),
                 market="moneyline", model="normal", stake="half_kelly")


def test_zscore_and_composite_order():
    z = byoe.zscore_index(STATS)
    comps = {c: byoe.composite(z, c, EDGE) for c in STATS}
    assert comps["A"] > comps["B"] > comps["C"] > comps["D"]


def test_score_game_normal_picks_stronger_team():
    z = byoe.zscore_index(STATS)
    pick = byoe.score_game(EDGE, NFL, z, home="A", away="D")
    assert pick.pick == "A"
    assert pick.prob > 0.5
    assert pick.margin > 0


def test_score_game_missing_team():
    z = byoe.zscore_index(STATS)
    assert byoe.score_game(EDGE, NFL, z, home="A", away="ZZZ") is None


def test_total_market_weighted():
    z = byoe.zscore_index(STATS)
    e = byoe.Edge("o", (byoe.EdgeInput("off", 1.0),), market="total", model="weighted")
    p = byoe.score_game(e, NFL, z, home="A", away="B")
    assert p.pick in ("OVER", "UNDER")
    assert p.prob is None


def test_invalid_config_rejected():
    with pytest.raises(ValueError):
        byoe.Edge("x", (byoe.EdgeInput("off", 1.0),), market="parlay")


def test_kelly_fraction_zero_when_no_edge():
    # fair price for 50% is +100; at -200 (implied 66%) a 50% prob has no edge
    assert byoe.kelly_fraction(0.5, -200) == 0.0
    assert byoe.kelly_fraction(0.6, 100) > 0.0


def test_stake_flat_and_cap():
    z = byoe.zscore_index(STATS)
    flat_edge = byoe.Edge("f", EDGE.inputs, model="weighted", stake="flat")
    pick = byoe.score_game(flat_edge, NFL, z, "A", "D")
    assert byoe.stake_units(pick, -110, flat_edge) == 1.0  # weighted -> flat


def test_backtest_grades_and_profits_on_strong_edge():
    z = byoe.zscore_index(STATS)
    # A (strong) hosts D (weak) repeatedly and wins; edge should profit.
    games = [{"date": f"2025-09-{d:02d}", "home": "A", "away": "D",
              "home_score": 30, "away_score": 13, "home_ml": -150} for d in range(1, 11)]
    res = byoe.backtest(EDGE, NFL, games, z_index_fn=lambda _d: z)
    assert res.n == 10
    assert res.wins == 10
    assert res.units > 0
    assert res.summary()["record"].startswith("10-0")
    assert len(res.equity) == 10


def test_odds_for_uses_real_market_odds():
    z = byoe.zscore_index(STATS)
    g = {"home": "A", "away": "D", "home_ml": -200, "away_ml": 170,
         "spread": -3.0, "spread_home_odds": -115, "spread_away_odds": -105,
         "total": 44.0, "total_over_odds": -108, "total_under_odds": -112}
    ml = byoe.Edge("ml", EDGE.inputs, market="moneyline", model="normal")
    sp = byoe.Edge("sp", EDGE.inputs, market="spread", model="normal")
    to = byoe.Edge("to", EDGE.inputs, market="total", model="normal")
    home_pick = byoe.Pick("A", 0.6, 5.0)
    over_pick = byoe.Pick("OVER", 0.55, None)
    assert byoe._odds_for(home_pick, ml, g) == -200
    assert byoe._odds_for(home_pick, sp, g) == -115
    assert byoe._odds_for(over_pick, to, g) == -108
    # missing odds -> -110 default
    assert byoe._odds_for(home_pick, ml, {"home": "A", "away": "D"}) == -110


def test_backtest_uses_attached_closing_odds():
    z = byoe.zscore_index(STATS)
    # underdog ML win at +150 pays more than the -110 default would imply
    flat = byoe.Edge("f", EDGE.inputs, market="moneyline", model="normal", stake="flat")
    games = [{"date": "2025-09-01", "home": "D", "away": "A",
              "home_score": 17, "away_score": 31, "away_ml": -150,
              "home_ml": 130}]
    res = byoe.backtest(flat, NFL, games, z_index_fn=lambda _d: z)
    assert res.n == 1 and res.wins == 1
    # A (away) was the pick and won at -150 -> 1u profit = 100/150 ~ 0.667u
    assert res.units == pytest.approx(100 / 150, abs=1e-6)


def test_backtest_handles_pushes_on_spread():
    z = byoe.zscore_index(STATS)
    games = [{"date": "2025-09-01", "home": "A", "away": "D",
              "home_score": 20, "away_score": 17, "spread": -3.0}]
    e = byoe.Edge("s", EDGE.inputs, market="spread", model="weighted")
    res = byoe.backtest(e, NFL, games, z_index_fn=lambda _d: z)
    assert res.pushes == 1  # home favored by 3, wins by exactly 3 -> push


# --- Ask-AI modes ----------------------------------------------------------

def test_modes_registry_complete():
    assert set(ai_modes.MODE_ORDER) == set(ai_modes.MODES)
    assert ai_modes.DEFAULT_MODE in ai_modes.MODES


def test_system_for_mode_appends_posture():
    base = "BASE SYSTEM"
    aggr = ai_modes.system_for_mode(base, "aggressive")
    cons = ai_modes.system_for_mode(base, "conservative")
    assert base in aggr and "AGGRESSIVE" in aggr
    assert "CONSERVATIVE" in cons
    assert aggr != cons


def test_system_for_mode_defaults_on_unknown():
    out = ai_modes.system_for_mode("B", "nonsense")
    assert "STANDARD" in out
