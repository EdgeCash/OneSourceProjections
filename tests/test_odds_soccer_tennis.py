"""Odds API wiring for soccer/tennis: sport-key mapping, tennis tournament
discovery, and the model<->odds EV join. The Odds API key is production-only, so
these drive the parsing/EV logic with synthetic responses."""
from __future__ import annotations

import pandas as pd
import pytest

from project547 import odds, pipeline
from project547.clients import oddsapi


def test_soccer_sport_keys_mapped():
    assert oddsapi.SPORT_KEYS["MLS"] == "soccer_usa_mls"
    assert oddsapi.SPORT_KEYS["EPL"] == "soccer_epl"


def test_new_sports_refresh_twice_daily():
    from project547 import config
    # soccer/tennis are slow (twice-daily); core US sports stay hourly
    assert oddsapi._sport_ttl("MLS") == config.ODDS_API_SLOW_TTL
    assert oddsapi._sport_ttl("EPL") == config.ODDS_API_SLOW_TTL
    assert oddsapi._sport_ttl("ATP") == config.ODDS_API_SLOW_TTL
    assert oddsapi._sport_ttl("MLB") == config.ODDS_API_TTL
    assert config.ODDS_API_SLOW_TTL == 12 * 60 * 60
    # the cache bucket rotates once per TTL window (so a 12h TTL == 2 fetches/day)
    slow, hourly = oddsapi._bucket(43200), oddsapi._bucket(3300)
    assert isinstance(slow, int) and isinstance(hourly, int)


def test_tennis_key_discovery(monkeypatch):
    monkeypatch.setattr(oddsapi, "list_sports", lambda: [
        {"key": "tennis_atp_wimbledon", "active": True},
        {"key": "tennis_atp_us_open", "active": False},      # not live -> excluded
        {"key": "tennis_wta_wimbledon", "active": True},     # wrong tour
        {"key": "soccer_epl", "active": True},               # not tennis
    ])
    assert oddsapi.tennis_sport_keys("ATP") == ["tennis_atp_wimbledon"]
    assert oddsapi.tennis_sport_keys("WTA") == ["tennis_wta_wimbledon"]
    assert oddsapi.tennis_sport_keys("MLB") == []


def test_fair_multiway_devigs_and_rejects():
    f = odds.fair_multiway({"home": -120, "draw": 260, "away": 320})
    assert sum(f.values()) == pytest.approx(1.0, abs=1e-6)
    assert f["home"] > f["away"] > 0
    assert odds.fair_multiway({"home": -5000, "draw": -5000, "away": -5000}) is None
    assert odds.fair_multiway({"home": -120}) is None      # need >=2 sides


def _soccer_event():
    return [{
        "home_team": "Inter Miami CF", "away_team": "Sporting Kansas City",
        "bookmakers": [{"key": "dk", "markets": [
            {"key": "h2h", "outcomes": [
                {"name": "Inter Miami CF", "price": -200},
                {"name": "Sporting Kansas City", "price": 500},
                {"name": "Draw", "price": 350}]},
            {"key": "totals", "outcomes": [
                {"name": "Over", "price": -110, "point": 2.5},
                {"name": "Under", "price": -110, "point": 2.5}]},
        ]}],
    }]


def test_attach_soccer_edges(monkeypatch):
    monkeypatch.setattr(pipeline.oddsapi, "game_odds",
                        lambda *a, **k: _soccer_event())
    df = pd.DataFrame([{
        "home_team": "Inter Miami CF", "away_team": "Sporting Kansas City",
        "home_win_prob": 0.84, "draw_prob": 0.10, "away_win_prob": 0.06,
        "over_2_5": 0.62,
    }])
    out = pipeline._attach_soccer_edges(df, "MLS", "2026-07-02")
    r = out.iloc[0]
    assert r["home_ml"] == -200 and r["draw_ml"] == 350 and r["away_ml"] == 500
    # model strongly favours home (0.84 vs devig ~0.63); even after the 0.5 market
    # shrink that clears the -200 breakeven -> positive home EV
    assert r["home_ev"] > 0
    assert r["over_price"] == -110 and r["over_ev"] is not None
    assert r["market"] in ("soccer_moneyline", "soccer_total")
    assert r["kelly"] is None or r["kelly"] >= 0


def test_attach_soccer_edges_no_odds_is_safe(monkeypatch):
    monkeypatch.setattr(pipeline.oddsapi, "game_odds", lambda *a, **k: [])
    df = pd.DataFrame([{"home_team": "A", "away_team": "B", "home_win_prob": 0.5,
                        "draw_prob": 0.25, "away_win_prob": 0.25, "over_2_5": 0.5}])
    out = pipeline._attach_soccer_edges(df, "MLS", "2026-07-02")
    assert out.iloc[0]["home_ev"] is None and out.iloc[0]["kelly"] is None


def _tennis_event():
    return [{
        "home_team": "Alexander Zverev", "away_team": "Taylor Fritz",
        "bookmakers": [{"key": "dk", "markets": [
            {"key": "h2h", "outcomes": [
                {"name": "Alexander Zverev", "price": -140},
                {"name": "Taylor Fritz", "price": 120}]}]}],
    }]


def test_attach_tennis_edges(monkeypatch):
    monkeypatch.setattr(pipeline.oddsapi, "game_odds",
                        lambda *a, **k: _tennis_event())
    df = pd.DataFrame([{
        "player1": "Alexander Zverev", "player2": "Taylor Fritz",
        "player1_win_prob": 0.65, "player2_win_prob": 0.35,
    }])
    out = pipeline._attach_tennis_edges(df, "ATP", "2026-06-20")
    r = out.iloc[0]
    assert r["p1_price"] == -140 and r["p2_price"] == 120
    assert r["p1_ev"] is not None and r["p2_ev"] is not None
    assert r["market"] == "tennis_moneyline"
    # model 0.65 beats the ~0.58 devig on p1 -> p1 EV should be the positive side
    assert r["p1_ev"] > r["p2_ev"]
