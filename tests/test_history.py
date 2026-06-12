"""Sanity checks that the imported historical data loads and has the
expected shape. These exercise the real files in data/history/."""

from onesource import history


def test_closing_lines_load():
    df = history.closing_lines("mlb")
    assert len(df) > 70_000
    assert {"event_id", "market", "side", "american_odds", "book"} <= set(df.columns)
    assert set(df["market"].unique()) >= {"moneyline", "total"}
    for sport, minimum in (("wnba", 20_000), ("nba", 10_000), ("nhl", 10_000)):
        assert len(history.closing_lines(sport)) > minimum


def test_results_load():
    df = history.results("mlb", season=2026)
    assert len(df) > 1_000
    assert {"home_team", "away_team", "home_score", "away_score"} <= set(df.columns)


def test_backfill_games():
    mlb = history.backfill_games("mlb", seasons=[2024])
    assert len(mlb) > 2_000
    wnba = history.backfill_games("wnba")
    assert wnba["season"].min() == 2002


def test_player_games():
    df = history.player_games("wnba", seasons=[2024])
    assert len(df) > 4_000
    assert {"player_name", "points", "rebounds", "assists"} <= set(df.columns)


def test_statcast_xstats():
    x = history.statcast_xstats(2024)
    assert len(x.get("batting", {})) > 400
    assert len(x.get("pitching", {})) > 400


def test_wnba_elo():
    df = history.wnba_elo()
    assert len(df) > 5_000
    assert "elo_home_winprob" in df.columns


def test_legacy_backtests_and_calibration():
    props = history.legacy_backtest("props_detail")
    assert len(props) > 500_000
    assert {"prop", "projection", "actual"} <= set(props.columns)
    cal = history.legacy_calibration("props")
    assert "pitcher_strikeouts" in cal.get("markets", {})


def test_graded_props_2026():
    df = history.graded_props_2026()
    assert {"Player", "Prop_Type", "Line", "Result"} <= set(df.columns)
