"""Forward-test grading: archive a slate, feed finals, and confirm the
ledger accrues calibration (Brier) + graded bet rows, idempotently."""

from onesource import results


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


def _wire(tmp_path, monkeypatch):
    monkeypatch.setattr(results, "PROJ_DIR", tmp_path / "proj")
    monkeypatch.setattr(results, "LEDGER", tmp_path / "results.jsonl")
    monkeypatch.setattr(results, "_finals", _finals)


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


def test_performance_summary_shape(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    results.archive_projections("2026-06-12", _slate())
    results.grade_date("2026-06-12")
    perf = results.performance()
    assert perf["overall"]["graded_games"] == 2
    assert perf["overall"]["model_brier"] is not None
    assert perf["overall"]["bets"] >= 1
    assert "MLB" in perf["by_sport"]
