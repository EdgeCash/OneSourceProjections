"""Forward-test grading: archive a slate, feed finals, and confirm the
ledger accrues calibration (Brier) + graded bet rows, idempotently."""

from project547 import results


def _slate():
    return {
        "MLB": {"games": [
            {  # home favorite that wins -> ML bet graded a winner
                "game_pk": 1, "home_team": "Boston Red Sox",
                "away_team": "New York Yankees", "home_win_prob": 0.60,
                "home_ml": -130, "home_ml_ev": 0.08, "away_ml": 110,
                "away_ml_ev": -0.06, "proj_total": 8.4,
                "total_line": 8.5, "over_odds": -105, "over_ev": 0.05,
            },
            {  # no edge -> only the win-prob (Brier) row, no bet
                "game_pk": 2, "home_team": "Chicago Cubs",
                "away_team": "St. Louis Cardinals", "home_win_prob": 0.52,
                "home_ml": -120, "home_ml_ev": -0.01, "away_ml": 100,
                "away_ml_ev": -0.02,
            },
        ]},
    }


def _finals(_sport, _date):
    return [
        {"game_pk": 1, "home_team": "Boston Red Sox",
         "away_team": "New York Yankees", "home_score": 6, "away_score": 2},
        {"game_pk": 2, "home_team": "Chicago Cubs",
         "away_team": "St. Louis Cardinals", "home_score": 1, "away_score": 4},
    ]


def _wire(tmp_path, monkeypatch, closes=None):
    monkeypatch.setattr(results, "PROJ_DIR", tmp_path / "proj")
    monkeypatch.setattr(results, "LEDGER", tmp_path / "results.jsonl")
    monkeypatch.setattr(results, "_finals", _finals)
    monkeypatch.setattr(results, "_closing_lines", lambda s, d: closes or {})
    # default: no first-inning data (avoids a real statsapi call); the NRFI
    # test overrides this with a stub.
    monkeypatch.setattr(results, "_first_inning", lambda s, d: {})


def test_grade_date_records_brier_and_bets(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    results.archive_projections("2026-06-12", _slate())

    n = results.grade_date("2026-06-12")
    rows = results.load_ledger()
    briers = [r for r in rows if r["market"] == "model_winprob"]
    bets = [r for r in rows if "pnl" in r]

    assert n == len(rows)
    # both games tracked for calibration regardless of a bet
    assert len(briers) == 2
    # only the +EV side(s) of game 1 become bets (ML over threshold, total too)
    assert {(b["game"], b["market"], b["side"]) for b in bets} == {
        ("New York Yankees @ Boston Red Sox", "moneyline", "home"),
        ("New York Yankees @ Boston Red Sox", "total", "over"),
    }
    # the home ML bet won; pnl is positive at -130
    ml = next(b for b in bets if b["market"] == "moneyline")
    assert ml["won"] is True and ml["pnl"] > 0


def test_grade_date_records_nrfi_calibration(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    # game 1 carries a model P(YRFI); the first inning had a run -> yrfi=1
    monkeypatch.setattr(results, "_first_inning", lambda s, d: {
        1: {"yrfi": 1, "key": frozenset({"red sox", "yankees"})},
        2: {"yrfi": 0, "key": frozenset({"cubs", "cardinals"})},
    })
    slate = _slate()
    slate["MLB"]["games"][0]["model_yrfi_prob"] = 0.55
    slate["MLB"]["games"][1]["model_yrfi_prob"] = 0.48
    results.archive_projections("2026-06-12", slate)
    results.grade_date("2026-06-12")

    nrfi = [r for r in results.load_ledger() if r["market"] == "model_nrfi"]
    assert len(nrfi) == 2
    g1 = next(r for r in nrfi if r["game"].endswith("Red Sox"))
    assert g1["yrfi"] == 1 and abs(g1["brier"] - (0.55 - 1) ** 2) < 1e-9
    # calibration summary reads the ledger
    cal = results.nrfi_calibration()
    assert cal["n"] == 2 and cal["model_brier"] is not None


def test_grade_date_is_idempotent(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    results.archive_projections("2026-06-12", _slate())
    first = results.grade_date("2026-06-12")
    second = results.grade_date("2026-06-12")
    assert first > 0 and second == 0
    assert len(results.load_ledger()) == first


def test_grade_recent_sweeps_window(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    results.archive_projections("2026-06-10", _slate())
    results.archive_projections("2026-06-12", _slate())
    # window covers both archived days (and tolerates the un-archived gap)
    total = results.grade_recent("2026-06-13", days=4)
    dates = {r["date"] for r in results.load_ledger()}
    assert dates == {"2026-06-10", "2026-06-12"}
    assert total == len(results.load_ledger())


def test_grade_attaches_clv_from_closing_lines(tmp_path, monkeypatch):
    from project547.names import normalize
    # closing line has the home side fair at 50% — our bet was at -130 (56.5%
    # implied), so we got a WORSE price than the close -> negative CLV.
    closes = {
        frozenset({normalize("Boston Red Sox"), normalize("New York Yankees")}): {
            "moneyline": {normalize("Boston Red Sox"): 0.50,
                          normalize("New York Yankees"): 0.50},
            "total": {"line": 8.5, "over": 0.55, "under": 0.45},
        }
    }
    _wire(tmp_path, monkeypatch, closes=closes)
    results.archive_projections("2026-06-12", _slate())
    results.grade_date("2026-06-12")

    bets = [r for r in results.load_ledger() if "pnl" in r]
    ml = next(b for b in bets if b["market"] == "moneyline")
    # CLV = EV at the closing fair prob: 0.50 at -130 is negative
    assert ml["clv"] is not None and ml["clv"] < 0
    tot = next(b for b in bets if b["market"] == "total")
    # over bet at -105 with a 55% closing fair prob -> positive CLV
    assert tot["clv"] is not None and tot["clv"] > 0

    perf = results.performance()["overall"]
    assert perf["clv_bets"] == len(bets)
    assert perf["avg_clv_pct"] is not None and perf["clv_beat_rate"] is not None


def test_performance_summary_shape(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    results.archive_projections("2026-06-12", _slate())
    results.grade_date("2026-06-12")
    perf = results.performance()
    assert perf["overall"]["graded_games"] == 2
    assert perf["overall"]["model_brier"] is not None
    assert perf["overall"]["bets"] >= 1
    assert "MLB" in perf["by_sport"]


def test_log_loss_computed(tmp_path, monkeypatch):
    import math
    _wire(tmp_path, monkeypatch)
    results.archive_projections("2026-06-12", _slate())
    results.grade_date("2026-06-12")
    ll = results.performance()["overall"]["model_log_loss"]
    # game1: pred 0.60, home won (y=1); game2: pred 0.52, home lost (y=0)
    expected = (-math.log(0.60) - math.log(1 - 0.52)) / 2
    assert abs(ll - expected) < 1e-3


# ---------------------------------------------------------------------------
# audit #17/#22: unders + spreads graded symmetrically, pushes push, ties push
# ---------------------------------------------------------------------------

def _slate_full():
    """One MLB game staking under + run line, one generic game staking spread."""
    return {
        "MLB": {"games": [
            {"game_pk": 11, "home_team": "Boston Red Sox",
             "away_team": "New York Yankees", "home_win_prob": 0.55,
             "proj_total": 7.6, "model_over_prob": 0.41,
             "total_line": 8.5, "over_odds": -110, "over_ev": -0.03,
             "under_odds": -105, "under_ev": 0.05,
             "rl_home_line": -1.5, "rl_home_odds": 130, "rl_home_ev": 0.04,
             "rl_away_odds": -150, "rl_away_ev": -0.02, "model_home_rl": 0.47},
        ]},
        "NFL": {"games": [
            {"game_id": 77, "home_team": "Dallas Cowboys",
             "away_team": "New York Giants", "home_win_prob": 0.60,
             "spread_home_line": -3.0, "spread_home_odds": -110,
             "spread_home_ev": 0.03, "spread_away_odds": -110,
             "spread_away_ev": -0.05, "model_home_cover": 0.55,
             "home_ml": -160, "home_ml_ev": 0.03, "away_ml": 140,
             "away_ml_ev": -0.04},
        ]},
    }


def _finals_full(sport, _date):
    if sport == "MLB":
        # total 6 (under wins), margin 4 (home -1.5 covers)
        return [{"game_pk": 11, "home_team": "Boston Red Sox",
                 "away_team": "New York Yankees", "home_score": 5, "away_score": 1}]
    # margin exactly 3 -> spread push; home ML wins
    return [{"game_id": 77, "home_team": "Dallas Cowboys",
             "away_team": "New York Giants", "home_score": 23, "away_score": 20}]


def test_unders_and_spreads_graded_with_pushes(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    monkeypatch.setattr(results, "_finals", _finals_full)
    results.archive_projections("2026-06-12", _slate_full())
    results.grade_date("2026-06-12")
    bets = {(r["sport"], r["market"], r["side"]): r
            for r in results.load_ledger() if "pnl" in r}

    und = bets[("MLB", "total", "under")]
    assert und["won"] is True and und["pnl"] > 0 and und["push"] is False
    assert und["line"] == 8.5
    assert und["model_prob"] == round(1 - 0.41, 4)
    assert ("MLB", "total", "over") not in bets      # over EV below the bar

    rl = bets[("MLB", "spread", "home")]
    assert rl["won"] is True and rl["line"] == -1.5 and rl["pnl"] > 0
    assert ("MLB", "spread", "away") not in bets     # negative EV

    sp = bets[("NFL", "spread", "home")]              # 23-20 on -3.0 = push
    assert sp["push"] is True and sp["won"] is None and sp["pnl"] == 0.0

    ml = bets[("NFL", "moneyline", "home")]
    assert ml["won"] is True


def test_total_push_refunds_stake(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    slate = _slate_full()
    monkeypatch.setattr(results, "_finals", lambda s, d: [
        {"game_pk": 11, "home_team": "Boston Red Sox",
         "away_team": "New York Yankees", "home_score": 5, "away_score": 3.5},
    ] if s == "MLB" else [])
    slate["MLB"]["games"][0]["total_line"] = 8.5
    results.archive_projections("2026-06-12", slate)
    results.grade_date("2026-06-12")
    und = next(r for r in results.load_ledger()
               if r.get("market") == "total" and r.get("side") == "under")
    assert und["push"] is True and und["won"] is None and und["pnl"] == 0.0
    # pushes don't count against the win rate / ROI denominators
    perf = results.performance()["by_sport"]["MLB"]
    assert perf["pushes"] >= 1


def test_tie_pushes_moneyline_and_skips_brier(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    monkeypatch.setattr(results, "_finals", lambda s, d: [
        {"game_id": 77, "home_team": "Dallas Cowboys",
         "away_team": "New York Giants", "home_score": 20, "away_score": 20},
    ] if s == "NFL" else [])
    results.archive_projections("2026-06-12", _slate_full())
    results.grade_date("2026-06-12")
    rows = results.load_ledger()
    ml = next(r for r in rows if r.get("market") == "moneyline")
    assert ml["push"] is True and ml["won"] is None and ml["pnl"] == 0.0
    # no model_winprob row for a tie — it's not a two-way outcome
    assert not [r for r in rows if r.get("market") == "model_winprob"
                and r.get("sport") == "NFL"]


def test_doubleheader_games_both_graded(tmp_path, monkeypatch):
    """Same date, same teams, two game_pks (a doubleheader) -> two ML rows."""
    _wire(tmp_path, monkeypatch)
    slate = {"MLB": {"games": [
        {"game_pk": 1, "home_team": "Boston Red Sox",
         "away_team": "New York Yankees", "home_win_prob": 0.6,
         "home_ml": -130, "home_ml_ev": 0.05},
        {"game_pk": 2, "home_team": "Boston Red Sox",
         "away_team": "New York Yankees", "home_win_prob": 0.58,
         "home_ml": -120, "home_ml_ev": 0.04},
    ]}}
    monkeypatch.setattr(results, "_finals", lambda s, d: [
        {"game_pk": 1, "home_team": "Boston Red Sox",
         "away_team": "New York Yankees", "home_score": 6, "away_score": 2},
        {"game_pk": 2, "home_team": "Boston Red Sox",
         "away_team": "New York Yankees", "home_score": 1, "away_score": 4},
    ])
    results.archive_projections("2026-06-12", slate)
    results.grade_date("2026-06-12")
    mls = [r for r in results.load_ledger() if r.get("market") == "moneyline"]
    assert len(mls) == 2
    assert {r["game_id"] for r in mls} == {1, 2}
    assert {r["won"] for r in mls} == {True, False}
    # and re-grading stays idempotent
    assert results.grade_date("2026-06-12") == 0


def test_total_clv_skipped_when_line_moved(tmp_path, monkeypatch):
    """A bet at 8.5 must not be scored against fair-at-9.5 (audit #18)."""
    from project547.names import normalize
    closes = {
        frozenset({normalize("Boston Red Sox"), normalize("New York Yankees")}): {
            "total": {"line": 9.5, "over": 0.52, "under": 0.48},
        }
    }
    _wire(tmp_path, monkeypatch, closes=closes)
    monkeypatch.setattr(results, "_finals", _finals_full)
    results.archive_projections("2026-06-12", _slate_full())
    results.grade_date("2026-06-12")
    und = next(r for r in results.load_ledger()
               if r.get("market") == "total" and r.get("side") == "under")
    assert und["clv"] is None
    assert und["clv_line_moved"] is True and und["close_line"] == 9.5


def test_total_clv_scored_at_same_line(tmp_path, monkeypatch):
    from project547.names import normalize
    closes = {
        frozenset({normalize("Boston Red Sox"), normalize("New York Yankees")}): {
            "total": {"line": 8.5, "over": 0.45, "under": 0.55},
        }
    }
    _wire(tmp_path, monkeypatch, closes=closes)
    monkeypatch.setattr(results, "_finals", _finals_full)
    results.archive_projections("2026-06-12", _slate_full())
    results.grade_date("2026-06-12")
    und = next(r for r in results.load_ledger()
               if r.get("market") == "total" and r.get("side") == "under")
    # under at -105 vs closing under fair .55 -> positive CLV
    assert und["clv"] is not None and und["clv"] > 0
    assert und["clv_line_moved"] is False
