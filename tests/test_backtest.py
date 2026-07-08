from project547 import backtest


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
    from project547.models import props
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


def test_detail_per_game_grading_consistency():
    """run_game_backtest(detail=True) emits per-game rows whose ML grade and
    fields are self-consistent (the data behind the replay UI)."""
    res = backtest.run_game_backtest("NFL", seasons=[2023], draws=200, detail=True)
    games = res["games"]
    assert len(games) > 100
    for g in games:
        assert {"date", "home", "away", "home_score", "away_score",
                "home_win_prob", "ml_fav", "ml_hit"} <= set(g)
        winner = (g["home"] if g["home_score"] > g["away_score"]
                  else g["away"] if g["away_score"] > g["home_score"] else None)
        if winner is not None:                       # NFL ties are rare
            assert g["ml_hit"] == (g["ml_fav"] == winner)
        assert g["ml_fav"] in (g["home"], g["away"])
        assert 0.0 <= g["home_win_prob"] <= 1.0


def test_prop_calibration_summary_artifact():
    """The committed summary the Prop-calibration UI reads stays well-formed
    and reflects the post-fix calibration (all gaps small)."""
    import json
    from project547 import config
    path = config.REPO_ROOT / "data" / "history" / "calibration" / "props_calibration_summary.json"
    cal = json.loads(path.read_text())
    assert "MLB" in cal and "batter_hits" in cal["MLB"]
    for sport, markets in cal.items():
        for mkt, c in markets.items():
            if not c.get("n"):
                continue
            assert {"projection_mae", "calibration_gap", "calibration"} <= set(c)
            assert abs(c["calibration_gap"]) < 0.05      # all markets well-behaved


# ---------------------------------------------------------------------------
# audit #18/#22: per-book same-line de-vig consensus in _devig_market
# ---------------------------------------------------------------------------

def test_devig_market_groups_by_book_and_line():
    from project547 import odds
    books = {
        # two books two-sided at 8.5 (the modal line)
        "dk": {"over": (-110, 8.5), "under": (-110, 8.5)},
        "fd": {"over": (-105, 8.5), "under": (-115, 8.5)},
        # one book at 9.5: its juicy over must not set the fair or best price
        "mgm": {"over": (+150, 9.5), "under": (-190, 9.5)},
    }
    fair, line, best_o, best_u = backtest._devig_market(books, "over", "under")
    assert line == 8.5
    # consensus fair over ~0.5 from the two 8.5 books, not dragged to ~0.42
    assert 0.48 < fair < 0.53
    # best prices only from books quoting 8.5 — the +150 at 9.5 is excluded
    assert best_o == -105 and best_u == -110


def test_devig_market_one_sided_book_prices_at_same_line_pool():
    books = {
        "dk": {"over": (-110, 8.5), "under": (-110, 8.5)},
        # one-sided book at the SAME line: eligible for best price, not fair
        "fd": {"over": (+100, 8.5)},
        # one-sided book at another line: fully excluded
        "mgm": {"over": (+140, 10.0)},
    }
    fair, line, best_o, best_u = backtest._devig_market(books, "over", "under")
    assert line == 8.5 and abs(fair - 0.5) < 0.01
    assert best_o == 100 and best_u == -110


def test_devig_market_moneyline_unlined():
    books = {
        "dk": {"home": (-150, None), "away": (+130, None)},
        "fd": {"home": (-145, None), "away": (+125, None)},
    }
    fair, line, best_h, best_a = backtest._devig_market(books, "home", "away")
    assert line is None
    assert 0.55 < fair < 0.62
    assert best_h == -145 and best_a == 130


def test_devig_market_spread_mirrored_lines():
    books = {"dk": {"home": (-110, -3.5), "away": (-110, 3.5)}}
    fair, line, best_h, best_a = backtest._devig_market(books, "home", "away")
    assert line == -3.5 and abs(fair - 0.5) < 1e-6


def test_devig_market_rejects_incoherent_pair():
    # implied sum ~0.56: stale/mismatched -> no consensus at all
    books = {"dk": {"over": (+104, 8.5), "under": (+1349, 8.5)}}
    assert backtest._devig_market(books, "over", "under") is None


# ---------------------------------------------------------------------------
# audit #22: ±1-day closing fallback requires line proximity; doubleheaders
# ---------------------------------------------------------------------------

def _rec(date, total_line):
    return {"date": date, "moneyline": {},
            "total": ({"line": total_line} if total_line is not None else None)}


def test_lookup_closing_one_day_fallback_requires_line_proximity():
    # game date has no record; the adjacent date's record is a DIFFERENT game
    # of the series with a very different total -> rejected
    consensus = {("red sox", "yankees"): [("2026-05-11", _rec("2026-05-11", 11.5))]}
    got = backtest._lookup_closing(consensus, "MLB", "2026-05-10",
                                   "Red Sox", "Yankees", ref_total=8.2)
    assert got is None
    # a nearby line is accepted
    consensus = {("red sox", "yankees"): [("2026-05-11", _rec("2026-05-11", 8.5))]}
    got = backtest._lookup_closing(consensus, "MLB", "2026-05-10",
                                   "Red Sox", "Yankees", ref_total=8.2)
    assert got is not None


def test_lookup_closing_exact_date_never_line_filtered():
    consensus = {("red sox", "yankees"): [("2026-05-10", _rec("2026-05-10", 12.5))]}
    got = backtest._lookup_closing(consensus, "MLB", "2026-05-10",
                                   "Red Sox", "Yankees", ref_total=7.0)
    assert got is not None


def test_lookup_closing_doubleheader_picks_closest_line():
    g1 = _rec("2026-05-10", 8.0)
    g2 = _rec("2026-05-10", 10.0)
    consensus = {("red sox", "yankees"): [("2026-05-10", g1), ("2026-05-10", g2)]}
    got = backtest._lookup_closing(consensus, "MLB", "2026-05-10",
                                   "Red Sox", "Yankees", ref_total=9.7)
    assert got is g2
    got = backtest._lookup_closing(consensus, "MLB", "2026-05-10",
                                   "Red Sox", "Yankees", ref_total=8.1)
    assert got is g1


# ---------------------------------------------------------------------------
# audit #19/#20: renamed bias metric, real per-bet CLV, production flags
# ---------------------------------------------------------------------------

def test_summary_has_home_prob_bias_and_bet_clv_fields():
    r = backtest.run_game_backtest("WNBA", [2026], draws=300, min_games=5)
    cl = r["closing_line"]
    assert "home_prob_bias" in cl and "avg_clv_vs_fair" not in cl
    assert "spread_home_prob_bias" in cl
    assert "avg_bet_clv" in cl
    for k in ("moneyline_bets", "total_bets", "spread_bets"):
        assert "avg_bet_clv" in cl[k]
    # defaults reproduce the historical backtest, not production
    assert r["production_mode"] is False and r["apply_calibration"] is False
    assert r["shrink"] == 0.0


def test_production_mode_uses_sport_shrink():
    from project547.sports import SPORTS
    r = backtest.run_game_backtest("WNBA", [2026], draws=200, min_games=5,
                                   production_mode=True)
    assert r["production_mode"] and r["apply_calibration"]
    assert r["shrink"] == SPORTS["WNBA"].market_shrink
    # explicit shrink still wins for sweeps
    r2 = backtest.run_game_backtest("WNBA", [2026], draws=200, min_games=5,
                                    production_mode=True, shrink=0.3)
    assert r2["shrink"] == 0.3


def test_betlog_tracks_per_bet_clv():
    b = backtest.BetLog()
    b.add(True, 2.0, clv=0.03)
    b.add(False, 2.0, clv=-0.01)
    b.add(True, 1.5)                 # no CLV info -> not in the average
    s = b.summary()
    assert s["avg_bet_clv"] == round((0.03 - 0.01) / 2, 4)


def test_nfl_tie_graded_as_no_pick():
    """Detail rows for tied finals: no ML grade (books push), home_won None."""
    res = backtest.run_game_backtest("NFL", seasons=[2022], draws=150, detail=True)
    ties = [g for g in res["games"] if g["home_score"] == g["away_score"]]
    for g in ties:
        assert g["ml_hit"] is None and g["home_won"] is None


def test_run_backtest_script_formats_new_fields():
    """The report script consumes the renamed fields without KeyError."""
    import importlib.util
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(
        "run_backtest", root / "scripts" / "run_backtest.py")
    rb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rb)
    summ = {"bets": 1, "win_rate": 1.0, "units": 0.5, "roi_pct": 50.0,
            "avg_bet_clv": 0.01}
    cl = {"games_matched": 3, "home_prob_bias": 0.01,
          "spread_home_prob_bias": None, "avg_bet_clv": 0.005,
          "moneyline_bets": summ, "total_bets": summ, "spread_bets": summ}
    out = rb._fmt_clv(cl)
    assert "home-prob bias" in out and "CLV" in out
    r = {"sport": "WNBA", "seasons": [2026], "n_games_graded": 10,
         "moneyline": {"brier": 0.2, "log_loss": 0.6, "favorite_hit_rate": 0.6},
         "total": {"mae": 10.0, "rmse": 12.0}, "calibration": [],
         "closing_line": cl}
    md = rb._md_game(r)
    assert "home-prob bias" in md and "per-bet CLV" in md


def test_betlog_clv_stats_mean_sd_lb():
    from project547.backtest import BetLog
    b = BetLog()
    for clv in (0.02, 0.04, 0.06, 0.08):     # mean 0.05
        b.add(won=True, dec_odds=1.9, clv=clv)
    s = b.clv_stats()
    assert s["clv_n"] == 4
    assert abs(s["avg_clv"] - 0.05) < 1e-9
    assert s["clv_sd"] is not None and s["clv_lb"] is not None
    assert s["clv_lb"] < s["avg_clv"]        # lower bound sits below the mean


def test_betlog_clv_stats_empty():
    from project547.backtest import BetLog
    s = BetLog().clv_stats()
    assert s == {"clv_n": 0, "avg_clv": None, "clv_sd": None, "clv_lb": None}


def test_band_clv_excludes_stale_line_bets():
    from project547 import backtest, config, odds
    # a curated-band bet logs CLV; a stale-line (ev >= STALE_EV) bet does not
    fair, price = 0.45, 300           # EV@fair = 0.8 (a longshot outlier)
    assert backtest._band_clv(config.STALE_EV - 0.001, fair, price) == odds.expected_value(fair, price)
    assert backtest._band_clv(config.STALE_EV, fair, price) is None
    assert backtest._band_clv(config.STALE_EV + 0.05, fair, price) is None
