from onesource import backtest


def test_team_key_mlb_distinguishes_sox():
    # the nickname approach collided Red Sox / White Sox; full-name norm
    # must keep them distinct
    red = backtest._team_key("MLB", "Boston Red Sox", "New York Yankees")
    white = backtest._team_key("MLB", "Chicago White Sox", "New York Yankees")
    assert red != white


def test_team_key_wnba_bridges_abbrev_and_full():
    abbr = backtest._team_key("WNBA", "MIN", "NY")
    full = backtest._team_key("WNBA", "Minnesota Lynx", "New York Liberty")
    assert abbr == full == ("lynx", "liberty")


def test_lookup_closing_tolerates_one_day():
    rec = {"date": "2026-05-10", "moneyline": {}}
    consensus = {("lynx", "liberty"): [("2026-05-10", rec)]}
    # exact, +1, -1 all hit; +2 misses
    assert backtest._lookup_closing(consensus, "WNBA", "2026-05-10", "MIN", "NY") is rec
    assert backtest._lookup_closing(consensus, "WNBA", "2026-05-11", "MIN", "NY") is rec
    assert backtest._lookup_closing(consensus, "WNBA", "2026-05-09", "MIN", "NY") is rec
    assert backtest._lookup_closing(consensus, "WNBA", "2026-05-12", "MIN", "NY") is None


def test_lookup_closing_prefers_exact_date():
    near = {"date": "2026-05-11", "tag": "near"}
    exact = {"date": "2026-05-10", "tag": "exact"}
    consensus = {("lynx", "liberty"): [("2026-05-11", near), ("2026-05-10", exact)]}
    got = backtest._lookup_closing(consensus, "WNBA", "2026-05-10", "MIN", "NY")
    assert got["tag"] == "exact"


def test_betlog_math():
    b = backtest.BetLog()
    b.add(True, 2.0)   # +1.0
    b.add(False, 2.0)  # -1.0
    b.add(True, 1.5)   # +0.5
    s = b.summary()
    assert s["bets"] == 3
    assert abs(s["units"] - 0.5) < 1e-9
    assert abs(s["roi_pct"] - (0.5 / 3 * 100)) < 0.01  # summary rounds to 2dp


def test_closing_consensus_loads_real_data():
    # smoke: real WNBA closing lines parse into the expected structure
    c = backtest.closing_consensus("WNBA")
    assert len(c) > 30
    rec = next(iter(c.values()))[0][1]
    assert "moneyline" in rec and "total" in rec
    if rec["moneyline"]:
        assert 0 < rec["moneyline"]["home_fair"] < 1


def test_small_game_backtest_runs():
    r = backtest.run_game_backtest("WNBA", [2024], draws=500, min_games=5)
    assert r["n_games_graded"] > 100
    assert r["moneyline"]["brier"] is not None
    assert 0 < r["moneyline"]["brier"] < 0.35
    assert r["total"]["mae"] is not None
