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


def test_bp_game_odds_open_close():
    df = history.bp_game_odds()
    assert len(df) > 9_000
    assert {"open_cost", "close_cost", "market_slug", "event_id"} <= set(df.columns)
    assert set(df["market_slug"].unique()) >= {"moneyline", "total", "run-line"}
    # both opening and closing prices present
    assert (df["open_cost"].notna() & df["close_cost"].notna()).all()


def test_bp_first_five_and_consensus():
    f5 = history.bp_first_five_odds()
    assert len(f5) > 9_000
    assert any("inning" in m for m in f5["market_slug"].unique())
    cc = history.closing_consensus_lines()
    assert {"ml_open_fair_home", "ml_close_fair_home"} <= set(cc.columns)


def test_mlb_starters_map():
    st = history.mlb_starters(2026)
    assert len(st["games"]) > 1_000
    assert len(st["pitchers"]) > 200
    g = st["games"][0]
    assert {"date", "away_sp_id", "home_sp_id"} <= set(g)


def test_pick_ledger():
    pl = history.pick_ledger("picks")
    assert len(pl) > 50
    assert {"date", "market", "side"} <= set(pl.columns)
