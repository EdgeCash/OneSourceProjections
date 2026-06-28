"""Tests for the MLB backfill assembly (scripts/build_mlb_backfill.py) and that
team_games('MLB') actually picks up the assembled current-season data."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "build_mlb_backfill", ROOT / "scripts" / "build_mlb_backfill.py")
bmb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bmb)


def test_pk_from_game_id():
    assert bmb._pk("MLB-STATS-825080") == "825080"
    assert bmb._pk(825080) == "825080"


def test_winner():
    assert bmb._winner("NYY", "BAL", 5, 2) == "NYY"
    assert bmb._winner("NYY", "BAL", 1, 4) == "BAL"
    assert bmb._winner("NYY", "BAL", 3, 3) is None      # tie -> no winner
    assert bmb._winner("NYY", "BAL", None, 2) is None


def test_derive_nrfi():
    assert bmb._derive_nrfi({"nrfi": True}) is True
    assert bmb._derive_nrfi({"nrfi": False}) is False
    # derive from first-inning runs when the flag is absent
    assert bmb._derive_nrfi({"f1_away": 0, "f1_home": 0}) is True
    assert bmb._derive_nrfi({"f1_away": 0, "f1_home": 2}) is False
    assert bmb._derive_nrfi({}) is None                 # unknown -> None


def test_assemble_2026_matches_schema_and_excludes_spring():
    rows = bmb.assemble(2026)
    if not rows:
        pytest.skip("2026 results/linescores not present in this checkout")
    # every regular-season row has the fields the model reads
    keys = set(rows[0])
    for col in ("date", "game_pk", "home_team", "away_team", "home_score",
                "away_score", "f1_away", "f1_home", "nrfi", "f5_winner",
                "total_runs", "rl_margin"):
        assert col in keys, col
    # spring training (pre-opening-day) is excluded: first row is opening day+
    assert rows[0]["date"] >= "2026-03-20"
    assert rows == sorted(rows, key=lambda r: r["date"])
    # canonical team abbreviations, not full statsapi names
    assert all(len(r["home_team"]) <= 3 for r in rows)


def test_team_games_includes_current_season():
    """Regression guard for the staleness bug: team_games('MLB') must surface
    2026, not silently fall back to 2025."""
    from project547 import teamstats
    df = teamstats.team_games("MLB", (2025, 2026))
    if df.empty:
        pytest.skip("no MLB backfill in this checkout")
    seasons = set(df["season"].unique())
    if (ROOT / "data/history/backfill/mlb/2026/games.json.gz").exists():
        assert 2026 in seasons
        d26 = df[df["season"] == 2026]
        assert d26["f1"].notna().any()  # first-inning data flowed through
