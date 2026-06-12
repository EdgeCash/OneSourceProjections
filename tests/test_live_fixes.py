"""Tests for the fixes driven by the first live runs: offers param
fallback, nested-projection parsing, prop-market resolution, ESPN range
chunking, always-present columns, and the analysis text."""

import json

import pandas as pd
import pytest
import requests

from app import ui
from onesource.clients import bettingpros, espn


# ---------------------------------------------------------------------------
# offers(): 400-fallback variants
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, code):
        self.status_code = code


def test_offers_fallback_tries_variants(monkeypatch):
    bettingpros._OFFERS_STYLE["idx"] = None
    calls = []

    def fake_get(path, params):
        calls.append(params)
        # fail anything with limit=100, succeed on the bare variant
        if "limit" in params or "location" in params:
            err = requests.HTTPError(response=_Resp(400))
            raise err
        return {"offers": [{"event_id": 1}]}

    monkeypatch.setattr(bettingpros, "_get", fake_get)
    out = bettingpros._offers_attempts("MLB", 122, [1, 2], "ALL", 2026)
    assert out["offers"] == [{"event_id": 1}]
    assert len(calls) >= 2  # tried at least one variant before succeeding
    # learned style is remembered and tried first next time
    idx = bettingpros._OFFERS_STYLE["idx"]
    assert idx is not None
    calls.clear()
    bettingpros._offers_attempts("MLB", 122, [1, 2], "ALL", 2026)
    assert "limit" not in calls[0] and "location" not in calls[0]
    bettingpros._OFFERS_STYLE["idx"] = None


def test_offers_fallback_exhausted_raises(monkeypatch):
    bettingpros._OFFERS_STYLE["idx"] = None

    def always_400(path, params):
        raise requests.HTTPError(response=_Resp(400))

    monkeypatch.setattr(bettingpros, "_get", always_400)
    with pytest.raises(bettingpros.BettingProsError):
        bettingpros._offers_attempts("MLB", 122, None, "ALL", None)
    bettingpros._OFFERS_STYLE["idx"] = None


# ---------------------------------------------------------------------------
# flatten_props: real (nested) live payload
# ---------------------------------------------------------------------------

LIVE_PROP = {
    "participant": "Jackson Chourio", "market_id": 403, "line": 1.5,
    "projection": {"recommended_side": "over", "value": 3.71,
                   "probability": 0.8847, "expected_value": 0.4242,
                   "bet_rating": 5, "diff": 2.21},
}


def test_flatten_props_parses_nested_projection():
    row = bettingpros.flatten_props([LIVE_PROP])[0]
    assert row["participant"] == "Jackson Chourio"
    assert row["bp_line"] == 1.5
    assert row["bp_projection"] == 3.71      # the nested value, not the dict
    assert row["bp_ev"] == pytest.approx(0.4242)
    assert row["bp_probability"] == pytest.approx(0.8847)
    assert row["bp_recommended_side"] == "over"
    assert row["bp_bet_rating"] == 5
    # numeric coercion: nothing in the row is a dict
    assert not any(isinstance(v, dict) for v in row.values())


def test_flatten_props_selection_shapes():
    p = dict(LIVE_PROP)
    p["selections"] = [
        {"selection": "over", "cost": -115, "line": 1.5},   # flat shape
        {"selection": "under",
         "books": [{"id": 10, "lines": [{"cost": -105, "line": 1.5,
                                         "active": True}]}]},  # nested shape
    ]
    row = bettingpros.flatten_props([p])[0]
    assert row["over_odds"] == -115
    assert row["under_odds"] == -105


# ---------------------------------------------------------------------------
# prop_market_ids keyword resolution
# ---------------------------------------------------------------------------

def test_prop_market_ids(monkeypatch):
    fake = {
        403: {"name": "Total Bases", "slug": "total-bases", "category": "player-props"},
        405: {"name": "Pitcher Outs", "slug": "pitcher-outs", "category": "player-props"},
        285: {"name": "Strikeouts Thrown", "slug": "strikeouts-thrown", "category": "player-props"},
        287: {"name": "Hits", "slug": "hits", "category": "player-props"},
        288: {"name": "Team Total Bases", "slug": "team-total-bases", "category": "team-props"},
        286: {"name": "Home Runs", "slug": "home-runs", "category": "player-props"},
    }
    monkeypatch.setattr(bettingpros, "market_lookup", lambda s: fake)
    ids = bettingpros.prop_market_ids("MLB")
    assert ids["pitcher_strikeouts"] == 285
    assert ids["batter_hits"] == 287
    assert ids["batter_total_bases"] == 403   # not the team variant (288)
    assert ids["batter_home_runs"] == 286


# ---------------------------------------------------------------------------
# ESPN long-range chunking
# ---------------------------------------------------------------------------

def test_results_range_chunks_long_windows(monkeypatch):
    seen = []

    def fake_get(sport, params):
        seen.append(params["dates"])
        return {"events": []}

    monkeypatch.setattr(espn, "_get", fake_get)
    monkeypatch.setattr(espn, "cached_json", lambda k, t, f: f())
    espn.results_range("WNBA", "2025-01-28", "2026-06-12")
    assert len(seen) >= 3  # ~500 days -> several <=150-day chunks
    for rng in seen:
        a, b = rng.split("-")
        from datetime import date
        d0 = date(int(a[:4]), int(a[4:6]), int(a[6:]))
        d1 = date(int(b[:4]), int(b[4:6]), int(b[6:]))
        assert (d1 - d0).days <= 150


# ---------------------------------------------------------------------------
# game-edge rows incl. run line / spread + analysis text
# ---------------------------------------------------------------------------

GAME = {
    "away_team": "New York Yankees", "home_team": "Boston Red Sox",
    "game_time": "2026-06-13T23:10:00Z", "away_exp_runs": 4.6,
    "home_exp_runs": 4.2, "proj_total": 8.8, "home_win_prob": 0.55,
    "away_win_prob": 0.45, "home_ml": -120, "home_ml_ev": 0.05,
    "away_ml": 110, "away_ml_ev": -0.04, "total_line": 8.5,
    "over_odds": -108, "under_odds": -112, "model_over_prob": 0.54,
    "over_ev": 0.03, "under_ev": -0.05,
    "rl_home_line": -1.5, "rl_home_odds": 142, "rl_away_odds": -165,
    "model_home_rl": 0.45, "rl_home_ev": 0.06, "rl_away_ev": -0.03,
}


def test_game_edges_include_run_line():
    rows = ui._game_edges("MLB", GAME)
    bets = {r["bet"] for r in rows}
    assert "Boston Red Sox -1.5" in bets
    assert "New York Yankees +1.5" in bets
    rl = next(r for r in rows if r["bet"] == "Boston Red Sox -1.5")
    assert rl["market"] == "Run Line"
    assert rl["model_prob"] == 0.45 and rl["ev"] == 0.06


def test_best_bets_board_has_spread(tmp_path):
    board = ui.build_best_bets({"MLB": {"games": [GAME], "props": []}}, 0.02)
    assert (board["market"] == "Run Line").any()
    # negative-EV sides excluded
    assert not board["bet"].str.contains("Yankees ML").any()


def test_matchup_analysis_text():
    m = {"away_off_vs_home_def": [
        {"stat": "Hits/G", "adv": 2, "off_rank": 3, "def_rank": 22}],
        "home_off_vs_away_def": []}
    rows = ui.matchup_analysis("MLB", GAME, m, 0.02)
    by_market = {r["market"]: r for r in rows}
    assert by_market["MONEYLINE"]["decision"] == "PLAY"     # +5% >= 2%
    assert by_market["RUN LINE"]["decision"] == "PLAY"      # +6%
    assert by_market["TOTAL"]["decision"] == "PLAY"         # +3%
    assert "55%" in by_market["MONEYLINE"]["text"]
    assert "EDGES" in by_market and "Hits/G" in by_market["EDGES"]["text"]


def test_analysis_pass_when_no_market():
    g = {k: v for k, v in GAME.items()
         if k not in ("home_ml", "away_ml", "home_ml_ev", "away_ml_ev",
                      "total_line", "over_odds", "over_ev", "under_odds",
                      "under_ev", "rl_home_line", "rl_home_odds",
                      "rl_away_odds", "model_home_rl", "rl_home_ev",
                      "rl_away_ev", "model_over_prob")}
    rows = ui.matchup_analysis("MLB", g, {}, 0.02)
    assert all(r["decision"] == "PASS" for r in rows)


# ---------------------------------------------------------------------------
# pipeline always-emits market columns (the live KeyError)
# ---------------------------------------------------------------------------

def test_props_have_line_column_even_without_offers(monkeypatch):
    from onesource import pipeline

    monkeypatch.setattr(bettingpros, "events",
                        lambda s, d: (_ for _ in ()).throw(RuntimeError("down")))
    props = pd.DataFrame([{"player": "X", "market": "pitcher_strikeouts",
                           "projection": 6.0, "dist": "poisson", "param": 6.0}])
    out = pipeline.attach_prop_edges(props, "2026-06-13")
    for col in ("line", "odds", "ev", "model_over_prob", "bp_projection"):
        assert col in out.columns
