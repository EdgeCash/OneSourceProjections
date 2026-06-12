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


def test_starter_fip_table_no_lookahead():
    fip = backtest.starter_fip_table([2024])
    vals = [v for v in fip.values() if v is not None]
    assert len(vals) > 3_000
    # FIP values land in a sane baseball range
    assert all(2.0 < v < 7.5 for v in vals)
    assert 3.7 < (sum(vals) / len(vals)) < 4.4  # ~league average
    # every entry is keyed by (game_pk:int, side)
    (pk, side) = next(iter(fip))
    assert isinstance(pk, int) and side in ("home", "away")


def test_starters_improve_or_match_calibration():
    tf = backtest.run_game_backtest("MLB", [2024], draws=800, use_starters=False)
    sp = backtest.run_game_backtest("MLB", [2024], draws=800, use_starters=True)
    assert sp["use_starters"] and not tf["use_starters"]
    # starters attached to nearly every game
    assert sp["games_with_starter"] > 0.9 * sp["n_games_graded"]
    # starter model should not be worse on Brier than team-form
    assert sp["moneyline"]["brier"] <= tf["moneyline"]["brier"] + 0.002


def test_bullpen_fip_table():
    bp = backtest.bullpen_fip_table([2024])
    vals = [v for v in bp.values() if v is not None]
    assert len(vals) > 2_000
    assert all(2.5 < v < 7.0 for v in vals)
    (pk, side) = next(iter(bp))
    assert isinstance(pk, int) and side in ("home", "away")


def test_full_model_beats_team_form():
    tf = backtest.run_game_backtest("MLB", [2024], draws=800)
    full = backtest.run_game_backtest("MLB", [2024], draws=800, use_starters=True,
                                      use_bullpen=True, use_park=True)
    # full pitching+park model should not be worse on Brier or total MAE
    assert full["moneyline"]["brier"] <= tf["moneyline"]["brier"] + 0.001
    assert full["total"]["mae"] <= tf["total"]["mae"] + 0.02


def test_mlb_prop_calibration():
    r = backtest.run_mlb_prop_calibration([2024, 2025])
    for mkt in ("pitcher_strikeouts", "batter_hits", "batter_total_bases",
                "batter_home_runs"):
        d = r[mkt]
        assert d["n"] > 1_000
        assert d["projection_mae"] is not None
        # all four production prop models should be reasonably calibrated
        assert abs(d["calibration_gap"]) < 0.05, (mkt, d["calibration_gap"])


def test_total_bases_neg_binom_beats_poisson():
    from onesource.models import props
    # overdispersed -> NB puts more mass at 0 and the tail, so for a low
    # half-line it gives a lower P(over) than Poisson at the same mean
    mean, line = 1.6, 1.5
    nb = props.prob_over_neg_binom(mean, line)
    po = props.prob_over_count(mean, line)
    assert nb < po


def test_bp_open_close_structure():
    bp = backtest.bp_open_close(2026)
    assert len(bp) > 800
    rec = next(r for r in bp.values() if r["moneyline"])
    m = rec["moneyline"]
    assert 0 < m["home_open_fair"] < 1
    assert abs(m["home_open_fair"] + m["away_open_fair"] - 1.0) < 1e-9


def test_clv_open_close_runs():
    c = backtest.run_mlb_clv_open_close([2024, 2025, 2026], draws=600)
    assert c["games_matched"] > 200
    assert c["moneyline"]["bets"] > 50
    assert c["moneyline"]["avg_clv"] is not None
    assert 0 <= c["moneyline"]["clv_positive_rate"] <= 1
