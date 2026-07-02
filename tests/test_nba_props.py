"""NBA prop model: market vocabulary, projection, and the shared opponent
adjustment. Distribution math is shared with the WNBA model (tested there)."""
from __future__ import annotations

import pytest

from project547.models import nba_props as nb


def test_market_aliases():
    assert nb.canonical_market("Player Points") == "points"
    assert nb.canonical_market("3-Pointers Made") == "threes"
    assert nb.canonical_market("PTS+REB+AST") == "pra"
    assert nb.canonical_market("") is None
    assert nb.canonical_market("Turnovers") is None


def test_project_uses_market_dispersion():
    proj = nb.project([20, 25, 18, 30, 22, 19, 27], "points")
    assert proj.market == "points"
    assert proj.n == 7
    assert proj.r == nb.MARKETS["points"]["r"]
    assert 15 < proj.proj < 30


def test_unknown_market_returns_none():
    assert nb.project([1, 2, 3], "Steals") is None


def _def_rows():
    # league 20 pts/appearance; TOUGH allows 14, SOFT 26, with enough sample.
    rows = []
    for _ in range(200):
        rows.append({"team": "TOUGH", "points": 14.0})
        rows.append({"team": "SOFT", "points": 26.0})
        rows.append({"team": "AVG", "points": 20.0})
    return rows


def test_opponent_factor_direction_and_clamp():
    tbl = nb.defense_factors(_def_rows())
    tough = nb.opponent_factor("Points", "TOUGH", tbl)
    soft = nb.opponent_factor("Points", "SOFT", tbl)
    assert tough < 1.0 < soft
    assert nb.opponent_factor("Points", "NOPE", tbl) == 1.0     # unknown team
    assert nb.opponent_factor("Steals", "SOFT", tbl) == 1.0     # unknown market
