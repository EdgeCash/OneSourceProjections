"""Offline tests for the hourly loop: snapshots, projection archiving, and
grading. API clients are stubbed so nothing hits the network."""

import json

import pytest

from onesource import results, snapshots
from onesource.clients import bettingpros, espn, mlb_statsapi


@pytest.fixture
def tmp_data(tmp_path, monkeypatch):
    """Redirect all output/ledger paths into a temp dir."""
    out = tmp_path / "output"
    proj = out / "projections"
    proj.mkdir(parents=True)
    monkeypatch.setattr(results, "PROJ_DIR", proj)
    monkeypatch.setattr(results, "LEDGER", tmp_path / "track" / "results.jsonl")
    monkeypatch.setattr(snapshots, "SNAP_DIR", tmp_path / "snap")
    return tmp_path


def test_snapshot_appends_timeseries(tmp_data, monkeypatch):
    monkeypatch.setattr(bettingpros, "events", lambda s, d: [{"id": 1}])
    monkeypatch.setattr(bettingpros, "game_market_ids", lambda s: {"moneyline": 10})
    monkeypatch.setattr(bettingpros, "offers",
                        lambda s, m, e=None, location="ALL", season=None: [])
    monkeypatch.setattr(bettingpros, "flatten_offers",
                        lambda o: [{"participant": "A", "odds": -120}])
    monkeypatch.setattr(bettingpros, "props", lambda s, d: [])
    monkeypatch.setattr(bettingpros, "flatten_props", lambda p: [])

    c1 = snapshots.snapshot("2026-06-13", sports=["MLB"])
    c2 = snapshots.snapshot("2026-06-13", sports=["MLB"])
    assert c1["MLB"] == 1 and c2["MLB"] == 1
    path = snapshots.SNAP_DIR / "mlb" / "2026-06-13.jsonl"
    # appended, not overwritten -> two rows, each timestamped
    lines = path.read_text().splitlines()
    assert len(lines) == 2
    assert all("captured_at" in json.loads(x) for x in lines)


def test_archive_and_grade_game(tmp_data, monkeypatch):
    slate = {"MLB": {"games": [{
        "game_pk": 777, "home_team": "Boston Red Sox", "away_team": "New York Yankees",
        "home_win_prob": 0.58, "proj_total": 8.5,
        "home_ml": -130, "home_ml_ev": 0.05,   # recommended bet on home
        "away_ml": 120, "away_ml_ev": -0.10,
        "total_line": 8.5, "over_odds": -110, "over_ev": 0.04,
    }], "props": []}}
    results.archive_projections("2026-06-12", slate)
    assert (results.PROJ_DIR / "2026-06-12.json").exists()

    # home wins 6-3 -> home ML wins, total 9 > 8.5 over wins
    monkeypatch.setattr(mlb_statsapi, "final_scores", lambda d: [{
        "game_pk": 777, "home_team": "Boston Red Sox", "away_team": "New York Yankees",
        "home_score": 6, "away_score": 3, "status": "final"}])

    n = results.grade_date("2026-06-12")
    assert n == 3  # winprob row + home ML bet + over bet
    rows = results.load_ledger()
    ml = next(r for r in rows if r["market"] == "moneyline")
    assert ml["won"] and ml["pnl"] > 0
    tot = next(r for r in rows if r["market"] == "total")
    assert tot["won"] and tot["pnl"] > 0
    wp = next(r for r in rows if r["market"] == "model_winprob")
    assert wp["home_won"] == 1 and wp["brier"] == round((0.58 - 1) ** 2, 4)


def test_grade_is_idempotent(tmp_data, monkeypatch):
    slate = {"MLB": {"games": [{
        "game_pk": 1, "home_team": "A Team", "away_team": "B Team",
        "home_win_prob": 0.5, "proj_total": 8}], "props": []}}
    results.archive_projections("2026-06-12", slate)
    monkeypatch.setattr(mlb_statsapi, "final_scores", lambda d: [{
        "game_pk": 1, "home_team": "A Team", "away_team": "B Team",
        "home_score": 5, "away_score": 4, "status": "final"}])
    assert results.grade_date("2026-06-12") == 1
    assert results.grade_date("2026-06-12") == 0  # already graded
    assert len(results.load_ledger()) == 1


def test_performance_summary(tmp_data):
    results._append([
        {"date": "d", "sport": "MLB", "game": "g", "market": "model_winprob",
         "side": "", "pred_home_wp": 0.6, "home_won": 1, "brier": 0.16},
        {"date": "d", "sport": "MLB", "game": "g", "market": "moneyline",
         "side": "home", "price": -110, "ev": 0.05, "won": True, "pnl": 0.91},
        {"date": "d", "sport": "MLB", "game": "g2", "market": "moneyline",
         "side": "home", "price": -110, "ev": 0.05, "won": False, "pnl": -1.0},
    ])
    perf = results.performance()
    o = perf["overall"]
    assert o["graded_games"] == 1 and o["bets"] == 2
    assert o["bet_win_rate"] == 0.5
    assert o["units"] == round(0.91 - 1.0, 2)
    assert "MLB" in perf["by_sport"]


def test_unfinished_game_not_graded(tmp_data, monkeypatch):
    slate = {"MLB": {"games": [{
        "game_pk": 9, "home_team": "A", "away_team": "B",
        "home_win_prob": 0.5, "proj_total": 8}], "props": []}}
    results.archive_projections("2026-06-12", slate)
    monkeypatch.setattr(mlb_statsapi, "final_scores", lambda d: [])  # none final
    assert results.grade_date("2026-06-12") == 0
