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


def test_projection_anchor_lookup():
    # enabled markets return their reviewed weight; unset markets return 0
    assert config.projection_anchor("NBA", "moneyline") == 0.5
    assert config.projection_anchor("MLB", "total") == 0.5
    assert config.projection_anchor("MLB", "spread") == 0.0   # spread not enabled
    assert config.projection_anchor("anything", "moneyline") == 0.0


def test_weight_zero_pub_equals_raw(monkeypatch):
    # the identity property: at weight 0 every *_pub equals its raw column
    monkeypatch.setattr(config, "PROJECTION_ANCHOR", {})
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


def _mlb_game():
    """An MLB game row: uses home_exp_runs/away_exp_runs (no home_exp/margin_mean)
    and rl_home_line, like the real MLB pipeline output."""
    return pd.DataFrame([{
        "home_win_prob": 0.55,
        "proj_total": 8.5,
        "home_exp_runs": 4.6,
        "away_exp_runs": 3.9,      # margin = +0.7
        "home_ml_fair": 0.51,
        "total_line": 9.0,
        "rl_home_line": -1.5,      # margin anchor = +1.5
    }])


def test_mlb_margin_and_side_scores_anchor(monkeypatch):
    # MLB run-line margin anchoring (follow-up): margin/side-scores must anchor
    # off home_exp_runs/away_exp_runs + rl_home_line, not be inert.
    monkeypatch.setattr(config, "PROJECTION_ANCHOR",
                        {("MLB", "spread"): 0.5, ("MLB", "total"): 0.5})
    out = pipeline._attach_anchored_projection(_mlb_game(), "MLB")
    r = out.iloc[0]
    # margin blends model +0.7 with anchor +1.5 at 0.5 -> +1.1
    assert r["margin_pub"] == pytest.approx(1.1)
    # total blends 8.5 with 9.0 -> 8.75; side scores reconstruct from tot+margin
    assert r["proj_total_pub"] == pytest.approx(8.75)
    assert r["home_exp_pub"] == pytest.approx((8.75 + 1.1) / 2)
    assert r["away_exp_pub"] == pytest.approx((8.75 - 1.1) / 2)


def test_mlb_margin_inert_at_zero_weight():
    # default weight 0 -> margin_pub is the raw model margin (home_exp_runs diff)
    out = pipeline._attach_anchored_projection(_mlb_game(), "MLB")
    assert out.iloc[0]["margin_pub"] == pytest.approx(0.7)
