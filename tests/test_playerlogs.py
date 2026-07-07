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


def _isolated(monkeypatch, df, sport_dirless=True):
    """Route _logs at a synthetic backfill frame with no forward store."""
    monkeypatch.setattr(pl.history, "player_games",
                        lambda sk, seasons=None: df.copy())
    monkeypatch.setattr(pl, "FORWARD_DIR", pl.FORWARD_DIR / "does-not-exist")
    pl._logs.cache_clear()


def test_role_filter_separates_two_way_player(monkeypatch):
    # Ohtani-style game: one batting line and one pitching line, same date +
    # game. pitcher_hits_allowed must read only the pitching row and
    # batter_hits only the batting row.
    df = pd.DataFrame([
        {"player_name": "Two Way", "date": "2026-06-01", "opponent": "SD",
         "season": 2026, "game_pk": 100, "role": "pitcher",
         "stats": {"hits": 6, "strikeOuts": 8, "inningsPitched": 6.0}},
        {"player_name": "Two Way", "date": "2026-06-01", "opponent": "SD",
         "season": 2026, "game_pk": 100, "role": "batter",
         "stats": {"hits": 1, "totalBases": 4, "homeRuns": 1}},
    ])
    _isolated(monkeypatch, df)
    try:
        hr_p = pl.hit_rates("MLB", "Two Way", "pitcher_hits_allowed", 3.5,
                            season=2026)
        hr_b = pl.hit_rates("MLB", "Two Way", "batter_hits", 3.5, season=2026)
        assert hr_p["season"] == 1.0    # 6 hits allowed > 3.5
        assert hr_b["season"] == 0.0    # 1 hit as a batter < 3.5
        assert pl.actual_value("MLB", "Two Way", "pitcher_hits_allowed",
                               "2026-06-01") == 6.0
        assert pl.actual_value("MLB", "Two Way", "batter_hits",
                               "2026-06-01") == 1.0
    finally:
        pl._logs.cache_clear()


def test_role_derived_from_forward_store_position(monkeypatch):
    # MLB forward-store rows carry position, not role — 'P' must count as the
    # pitching role so grading doesn't cross-read.
    df = pd.DataFrame([
        {"name": "Fwd Pitcher", "date": "2026-06-02", "opponent": "SD",
         "season": 2026, "game_pk": 200, "position": "P",
         "hits": 5, "strikeOuts": 7, "inningsPitched": 6.0},
        {"name": "Fwd Batter", "date": "2026-06-02", "opponent": "SD",
         "season": 2026, "game_pk": 200, "position": "3B",
         "hits": 2, "totalBases": 3},
    ])
    _isolated(monkeypatch, df)
    try:
        assert pl.actual_value("MLB", "Fwd Pitcher", "pitcher_hits_allowed",
                               "2026-06-02") == 5.0
        assert pl.actual_value("MLB", "Fwd Pitcher", "batter_hits",
                               "2026-06-02") is None
        assert pl.actual_value("MLB", "Fwd Batter", "batter_hits",
                               "2026-06-02") == 2.0
    finally:
        pl._logs.cache_clear()


def test_doubleheader_both_games_kept(monkeypatch):
    # two games on one date (MLB doubleheader) are distinct game_pks — the
    # dedupe must keep both, while a re-ingested duplicate row still dedupes.
    df = pd.DataFrame([
        {"player_name": "DH Player", "date": "2026-06-03", "opponent": "CHC",
         "season": 2026, "game_pk": 301, "role": "batter",
         "stats": {"hits": 2, "totalBases": 2}},
        {"player_name": "DH Player", "date": "2026-06-03", "opponent": "CHC",
         "season": 2026, "game_pk": 302, "role": "batter",
         "stats": {"hits": 0, "totalBases": 0}},
        {"player_name": "DH Player", "date": "2026-06-03", "opponent": "CHC",
         "season": 2026, "game_pk": 302, "role": "batter",
         "stats": {"hits": 0, "totalBases": 0}},   # duplicate ingest
    ])
    _isolated(monkeypatch, df)
    try:
        logs = pl._logs("MLB", (2025, 2026))
        assert len(logs) == 2           # game 1 + game 2, duplicate dropped
        hr = pl.hit_rates("MLB", "DH Player", "batter_hits", 0.5, season=2026)
        assert hr["season"] == 0.5      # over in game 1, under in game 2
        assert hr["n_l5"] == 2
    finally:
        pl._logs.cache_clear()


def test_dedupe_falls_back_to_norm_date_without_game_id(monkeypatch):
    df = pd.DataFrame([
        {"player_name": "No Gid", "date": "2026-06-04", "opponent": "X",
         "season": 2026, "points": 20.0},
        {"player_name": "No Gid", "date": "2026-06-04", "opponent": "X",
         "season": 2026, "points": 20.0},
    ])
    _isolated(monkeypatch, df)
    try:
        logs = pl._logs("WNBA", (2025, 2026))
        assert len(logs) == 1
    finally:
        pl._logs.cache_clear()


def test_actual_value_nhl_january_straddles_seasons(monkeypatch):
    # NHL logs are labeled by season START year: a 2026-01-10 game lives in
    # season 2025. actual_value must load {Y, Y-1} or Jan-Jun grading is None.
    df = pd.DataFrame([
        {"player_name": "Winter Skater", "date": "2026-01-10", "season": 2025,
         "opponent": "Dallas Stars", "game_id": "401000001", "position": "F",
         "goals": 1, "assists": 0, "points": 1, "shots": 4, "blocks": 2,
         "hits": 3, "pim": 0},
    ])
    monkeypatch.setattr(pl.history, "player_games",
                        lambda sk, seasons=None:
                        df[df["season"].isin(seasons or [])].copy())
    monkeypatch.setattr(pl, "FORWARD_DIR", pl.FORWARD_DIR / "does-not-exist")
    pl._logs.cache_clear()
    try:
        assert pl.actual_value("NHL", "Winter Skater", "shots on goal",
                               "2026-01-10") == 4.0
        assert pl.actual_value("NHL", "Winter Skater", "goals",
                               "2026-01-10") == 1.0
        assert pl.actual_value("NHL", "Winter Skater", "blocked shots",
                               "2026-01-10") == 2.0
    finally:
        pl._logs.cache_clear()


def test_ingest_dispatches_nhl_with_start_year_season(tmp_path, monkeypatch):
    import json

    from project547.clients import espn

    monkeypatch.setattr(pl, "FORWARD_DIR", tmp_path)
    monkeypatch.setattr(espn, "results_range", lambda sk, s, e: [
        {"game_id": "401777001", "completed": True,
         "home_score": 3.0, "away_score": 2.0}])
    monkeypatch.setattr(espn, "box_player_logs", lambda sk, eid: [
        {"game_pk": eid, "name": "Ingest Skater", "team": "Boston Bruins",
         "opponent": "Montreal Canadiens", "position": "F", "goals": 1,
         "assists": 1, "points": 2, "shots": 5, "blocks": 1, "hits": 2,
         "pim": 0}])
    pl._logs.cache_clear()
    try:
        assert pl.ingest("NHL", "2026-01-10") == 1     # dispatch exists
        assert pl.ingest("NHL", "2026-01-10") == 0     # idempotent by event id
        row = json.loads((tmp_path / "nhl.jsonl").read_text().splitlines()[0])
        assert row["season"] == 2025    # start-year label, matches backfill
        assert row["shots"] == 5 and row["date"] == "2026-01-10"
        # an October game keeps the calendar year
        monkeypatch.setattr(espn, "results_range", lambda sk, s, e: [
            {"game_id": "401777002", "completed": True,
             "home_score": 1.0, "away_score": 0.0}])
        assert pl.ingest("NHL", "2025-10-12") == 1
        row2 = json.loads((tmp_path / "nhl.jsonl").read_text().splitlines()[1])
        assert row2["season"] == 2025
    finally:
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
