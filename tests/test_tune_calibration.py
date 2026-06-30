"""Unit tests for the calibration drift monitor's pure helpers (no backtest)."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "tune_calibration", ROOT / "scripts" / "tune_calibration.py")
tc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tc)


def test_available_seasons_returns_recent_from_backfill():
    yrs = tc._available_seasons("MLB", 3)
    assert yrs == sorted(yrs)            # ascending
    assert len(yrs) <= 3
    assert all(2000 < y < 2100 for y in yrs)


def test_available_seasons_unknown_sport_is_empty():
    assert tc._available_seasons("CRICKET", 4) == []


def test_units_and_bets_tolerate_missing_keys():
    assert tc._units({}) == 0.0
    assert tc._bets({}) == 0
    assert tc._units({"units": 3.5}) == 3.5
    assert tc._bets({"bets": 12}) == 12


def _stub(sport, current, recommended, drift, matched):
    summ = lambda u, b, roi: {"units": u, "bets": b, "roi_pct": roi}
    row = {"shrink": current, "total_units": 1.0, "total_bets": matched,
           "ml": summ(1, matched, 5.0), "total": summ(0, 0, 0.0),
           "spread": summ(0, 0, 0.0)}
    best = dict(row, shrink=recommended, total_units=4.0)
    return {"sport": sport, "seasons": [2024, 2025], "current_shrink": current,
            "recommended_shrink": recommended, "drift": drift,
            "recommendation": (f"move {current} -> {recommended}" if drift
                               else f"hold {current}"),
            "matched_bets": matched, "n_games": 500, "brier": 0.25,
            "favorite_hit_rate": 0.55, "total_bias": -0.1, "total_mae": 3.5,
            "sweep": [row, best], "best": best if drift else None,
            "current_row": row if drift else None}


def test_md_builds_with_and_without_drift():
    drifted = _stub("MLB", 0.5, 0.8, True, 400)
    held = _stub("WNBA", 0.5, 0.5, False, 50)
    md = tc._md([drifted, held])
    assert "Calibration & MARKET_SHRINK drift" in md
    assert "MLB" in md and "WNBA" in md
    assert "Drift alerts" in md
    assert "0.5→0.8" in md            # drift shown in the table
    # no-drift sport must not appear in the alerts section
    alerts = md.split("Drift alerts")[1].split("Per-sport")[0]
    assert "WNBA" not in alerts


def test_md_no_drift_shows_placeholder():
    md = tc._md([_stub("NBA", 0.5, 0.5, False, 2000)])
    assert "No sport crossed the change threshold" in md
