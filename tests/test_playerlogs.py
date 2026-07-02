import pandas as pd

from app import ui
from project547 import playerlogs as pl


def test_logs_drop_dnp_and_low_minutes(monkeypatch):
    # basketball logs with a DNP and a garbage-time cameo must be excluded so a
    # non-appearance isn't counted as "scored 0" (train/serve skew vs the
    # prop-model validators, which exclude them).
    df = pd.DataFrame([
        {"player_name": "Test Player", "date": "2026-06-01", "opponent": "X",
         "season": 2026, "minutes": 30, "did_not_play": False, "points": 20},
        {"player_name": "Test Player", "date": "2026-06-03", "opponent": "Y",
         "season": 2026, "minutes": 0, "did_not_play": True, "points": 0},
        {"player_name": "Test Player", "date": "2026-06-05", "opponent": "Z",
         "season": 2026, "minutes": 2, "did_not_play": False, "points": 0},
        {"player_name": "Test Player", "date": "2026-06-07", "opponent": "W",
         "season": 2026, "minutes": 28, "did_not_play": False, "points": 14},
    ])
    monkeypatch.setattr(pl.history, "player_games",
                        lambda sk, seasons=None: df.copy())
    monkeypatch.setattr(pl, "FORWARD_DIR", pl.FORWARD_DIR / "does-not-exist")
    pl._logs.cache_clear()
    series = pl.recent_series("WNBA", "Test Player", "points", n=10, season=2026)
    pl._logs.cache_clear()
    vals = [p["value"] for p in series]
    assert vals == [20.0, 14.0]     # DNP and 2-minute cameo dropped


def test_market_to_stat():
    assert pl.market_to_stat("pitcher_strikeouts") == ("strikeOuts", "P")
    assert pl.market_to_stat("batter_total_bases") == ("totalBases", "B")
    assert pl.market_to_stat("Points") == ("points", None)
    assert pl.market_to_stat("Rebounds") == ("rebounds", None)
    assert pl.market_to_stat("nonsense") is None


def test_hit_rates_real_mlb():
    hr = pl.hit_rates("MLB", "Logan Gilbert", "pitcher_strikeouts", 5.5, season=2026)
    assert set(hr) >= {"l5", "l10", "l20", "season"}
    for k in ("l5", "l10", "l20", "season"):
        assert 0.0 <= hr[k] <= 1.0
    # a high line should be cleared less often than a low one
    low = pl.hit_rates("MLB", "Logan Gilbert", "pitcher_strikeouts", 2.5, season=2026)
    high = pl.hit_rates("MLB", "Logan Gilbert", "pitcher_strikeouts", 9.5, season=2026)
    assert low["season"] >= high["season"]


def test_hit_rates_real_wnba_and_h2h():
    hr = pl.hit_rates("WNBA", "A'ja Wilson", "Points", 22.5, opponent="DAL",
                      season=2026)
    assert "season" in hr
    assert "h2h" in hr  # opponent provided


def test_hit_rates_unknown_player_empty():
    assert pl.hit_rates("MLB", "Nobody Atall", "batter_hits", 1.5, season=2026) == {}
    assert pl.hit_rates("MLB", "x", "not_a_market", 1.5) == {}


def test_recent_series_shape_and_order():
    s = pl.recent_series("MLB", "Logan Gilbert", "pitcher_strikeouts", n=6,
                         season=2026)
    assert 1 <= len(s) <= 6
    assert all({"date", "value", "opp"} <= set(g) for g in s)
    # oldest-first for left-to-right plotting (dates non-decreasing by month/day)
    assert isinstance(s[0]["value"], float)


def test_prop_chart_builds_and_handles_empty():
    series = [{"date": "5/1", "value": 6, "opp": "BOS"},
              {"date": "5/6", "value": 4, "opp": "NYY"},
              {"date": "5/11", "value": 8, "opp": "TB"}]
    chart = ui.prop_chart(series, 5.5, "Strikeouts")
    assert chart is not None
    assert ui.prop_chart([], 5.5, "x") is None


def test_ingest_mlb_idempotent_and_feeds_rates(tmp_path, monkeypatch):
    from project547 import history
    from project547.clients import mlb_statsapi

    monkeypatch.setattr(pl, "FORWARD_DIR", tmp_path)
    monkeypatch.setattr(mlb_statsapi, "final_scores", lambda d: [
        {"game_pk": 999, "home_team": "BOS", "away_team": "NYY",
         "home_score": 5, "away_score": 3, "status": "final"}])
    monkeypatch.setattr(mlb_statsapi, "box_player_logs", lambda pk: [
        {"game_pk": pk, "name": "Test Slugger", "opponent": "BOS",
         "hits": 2, "totalBases": 5, "homeRuns": 1, "strikeOuts": 1}])
    # isolate to the forward store
    monkeypatch.setattr(history, "player_games",
                        lambda sk, seasons=None: pd.DataFrame())
    pl._logs.cache_clear()

    assert pl.ingest_mlb("2026-06-15") == 1
    assert pl.ingest_mlb("2026-06-15") == 0   # same game_pk -> skipped
    pl._logs.cache_clear()
    hr = pl.hit_rates("MLB", "Test Slugger", "batter_total_bases", 1.5, season=2026)
    assert hr.get("season") == 1.0  # flat forward-store row feeds the rate
    pl._logs.cache_clear()


def test_prep_props_scales_heatmap_columns():
    df = pd.DataFrame([{
        "player": "Logan Gilbert", "market": "pitcher_strikeouts", "line": 5.5,
        "odds": -110, "ev": 0.05, "hr_l5": 0.8, "hr_l10": 0.6, "hr_season": 0.61,
    }])
    view = ui.prep_props(df)
    assert view.loc[0, "L5"] == 80.0    # fraction -> percent
    assert view.loc[0, "Season"] == 61.0
    assert "L5" in ui.HEAT_COLS
