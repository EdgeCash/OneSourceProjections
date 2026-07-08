"""Market-anchored published projection (roadmap T3.1 / docs/CURATION_DESIGN.md
Component 4). The published ``*_pub`` columns blend the raw model projection
toward the market anchor per ``config.PROJECTION_ANCHOR``; default weight 0 must
leave every ``*_pub`` column exactly equal to its raw model column so nothing
changes live and edge detection (raw columns) is untouched."""

import pandas as pd
import pytest

from project547 import config, pipeline


def _games():
    """One synthetic generic-sport game with self-consistent raw columns
    (proj_total == home_exp + away_exp) plus the stored market anchors."""
    return pd.DataFrame([{
        "home_win_prob": 0.60,
        "away_win_prob": 0.40,
        "proj_total": 210.0,
        "home_exp": 108.0,
        "away_exp": 102.0,        # margin = +6
        "home_ml_fair": 0.50,     # moneyline anchor
        "total_line": 220.0,      # total anchor
        "spread_home_line": -4.0,  # margin anchor = -(-4) = +4
    }])


def test_projection_anchor_default_zero():
    assert config.projection_anchor("NBA", "moneyline") == 0.0
    assert config.projection_anchor("MLB", "total") == 0.0
    assert config.projection_anchor("anything", "spread") == 0.0


def test_weight_zero_pub_equals_raw():
    g = _games()
    out = pipeline._attach_anchored_projection(g.copy(), "NBA")
    row = out.iloc[0]
    assert row["home_win_prob_pub"] == pytest.approx(0.60)
    assert row["proj_total_pub"] == pytest.approx(210.0)
    assert row["margin_pub"] == pytest.approx(6.0)
    # side scores reconstruct from anchored total+margin, equal raw at weight 0
    assert row["home_exp_pub"] == pytest.approx(108.0)
    assert row["away_exp_pub"] == pytest.approx(102.0)


def test_moneyline_midpoint_at_half(monkeypatch):
    monkeypatch.setattr(config, "PROJECTION_ANCHOR", {("NBA", "moneyline"): 0.5})
    out = pipeline._attach_anchored_projection(_games(), "NBA")
    row = out.iloc[0]
    # midpoint of model 0.60 and market fair 0.50
    assert row["home_win_prob_pub"] == pytest.approx(0.55)
    # total/margin untouched — those markets have weight 0
    assert row["proj_total_pub"] == pytest.approx(210.0)
    assert row["margin_pub"] == pytest.approx(6.0)


def test_total_midpoint_at_half(monkeypatch):
    monkeypatch.setattr(config, "PROJECTION_ANCHOR", {("NBA", "total"): 0.5})
    out = pipeline._attach_anchored_projection(_games(), "NBA")
    row = out.iloc[0]
    # midpoint of model 210 and posted line 220
    assert row["proj_total_pub"] == pytest.approx(215.0)
    assert row["home_win_prob_pub"] == pytest.approx(0.60)


def test_margin_midpoint_and_side_scores(monkeypatch):
    monkeypatch.setattr(config, "PROJECTION_ANCHOR",
                        {("NBA", "spread"): 0.5, ("NBA", "total"): 0.5})
    out = pipeline._attach_anchored_projection(_games(), "NBA")
    row = out.iloc[0]
    # model margin +6, anchor -(-4) = +4 -> midpoint +5
    assert row["margin_pub"] == pytest.approx(5.0)
    assert row["proj_total_pub"] == pytest.approx(215.0)
    # home/away exp reconstruct from anchored total + margin
    assert row["home_exp_pub"] == pytest.approx((215.0 + 5.0) / 2)  # 110
    assert row["away_exp_pub"] == pytest.approx((215.0 - 5.0) / 2)  # 105


def test_missing_anchor_falls_back_to_raw(monkeypatch):
    # weight is set, but the anchor value is missing -> raw model number kept
    monkeypatch.setattr(config, "PROJECTION_ANCHOR", {("NBA", "moneyline"): 0.5})
    g = _games()
    g.loc[0, "home_ml_fair"] = None
    out = pipeline._attach_anchored_projection(g, "NBA")
    assert out.iloc[0]["home_win_prob_pub"] == pytest.approx(0.60)


def test_none_safe_margin_from_exp(monkeypatch):
    # no explicit margin_mean, margin derived from home_exp - away_exp; a missing
    # spread anchor still leaves margin_pub at the model margin.
    monkeypatch.setattr(config, "PROJECTION_ANCHOR", {("NBA", "spread"): 0.5})
    g = _games()
    g.loc[0, "spread_home_line"] = None
    out = pipeline._attach_anchored_projection(g, "NBA")
    assert out.iloc[0]["margin_pub"] == pytest.approx(6.0)


def test_empty_frame_is_noop():
    empty = pd.DataFrame()
    out = pipeline._attach_anchored_projection(empty, "NBA")
    for c in ("home_win_prob_pub", "proj_total_pub", "margin_pub",
              "home_exp_pub", "away_exp_pub"):
        assert c in out.columns
    assert out.empty


def test_margin_mean_preferred_over_exp_diff(monkeypatch):
    monkeypatch.setattr(config, "PROJECTION_ANCHOR", {("MLB", "spread"): 0.0})
    g = _games()
    g.loc[0, "margin_mean"] = 3.0  # differs from home_exp-away_exp (=6)
    out = pipeline._attach_anchored_projection(g, "MLB")
    assert out.iloc[0]["margin_pub"] == pytest.approx(3.0)
