"""Tests for baseline projections (de-vigged market + our-adjustment hook)."""

from __future__ import annotations

import pytest

from project547 import baseline


def test_two_way_market_baseline_devigs_to_one():
    b = baseline.market_baseline(-150, 130)
    assert b is not None and b.draw_wp is None
    assert b.home_wp > b.away_wp                      # -150 favorite
    assert b.home_wp + b.away_wp == pytest.approx(1.0, abs=1e-6)
    # no-vig home prob below the raw vig'd implied prob (~60%)
    assert 0.55 < b.home_wp < 0.60


def test_three_way_market_baseline_for_soccer():
    b = baseline.market_baseline(120, 200, draw_ml=240)
    assert b.draw_wp is not None
    assert b.home_wp + b.away_wp + b.draw_wp == pytest.approx(1.0, abs=1e-6)
    assert "Draw" in b.as_pct()


def test_market_baseline_missing_odds_returns_none():
    assert baseline.market_baseline(None, 130) is None
    assert baseline.market_baseline("", "") is None


def test_apply_edge_shifts_and_renormalizes():
    b = baseline.market_baseline(-110, -110)            # ~50/50
    adj = baseline.apply_edge(b, 0.05)                  # our math: +5pts home
    assert adj.source == "blend"
    assert adj.home_wp > b.home_wp and adj.away_wp < b.away_wp
    assert adj.home_wp + adj.away_wp == pytest.approx(1.0, abs=1e-6)
    # zero adjustment is a no-op (we can track the baseline before we model)
    assert baseline.apply_edge(b, 0.0) is b


def test_apply_edge_keeps_draw_and_renormalizes_three_way():
    b = baseline.market_baseline(120, 200, draw_ml=240)
    adj = baseline.apply_edge(b, 0.04)
    assert adj.draw_wp is not None
    assert adj.home_wp + adj.away_wp + adj.draw_wp == pytest.approx(1.0, abs=1e-6)
    assert adj.home_wp > b.home_wp


def test_bp_sport_mapping():
    assert baseline.bp_sport_for("Soccer") == "SOCCER"
    assert baseline.bp_sport_for("MMA") == "UFC"
    assert baseline.bp_sport_for("Lacrosse") is None   # no BP coverage
