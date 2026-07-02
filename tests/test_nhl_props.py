"""NHL skater-prop model: market vocabulary + projection. The distribution math
is shared with the WNBA model (tested in test_wnba_props); here we check the NHL
markets, aliases, and that project() wires the provisional dispersion through."""
from __future__ import annotations

import pytest

from project547.models import nhl_props as npx


def test_market_aliases():
    assert npx.canonical_market("Shots On Goal") == "shots"
    assert npx.canonical_market("SOG") == "shots"
    assert npx.canonical_market("Blocked Shots") == "blocks"
    assert npx.canonical_market("Player Goals") == "goals"
    assert npx.canonical_market("") is None
    assert npx.canonical_market("Faceoff Wins") is None


def test_project_uses_market_dispersion():
    proj = npx.project([2, 3, 1, 4, 2, 3, 2], "shots")
    assert proj.market == "shots"
    assert proj.n == 7
    assert proj.r == npx.MARKETS["shots"]["r"]
    assert 1 < proj.proj < 4


def test_prob_over_shared_math_is_sane():
    # reuses the WNBA NB survival; higher line -> lower P(over)
    lo = npx.prob_over(2.5, 3.5, npx.MARKETS["shots"]["r"])
    hi = npx.prob_over(2.5, 1.5, npx.MARKETS["shots"]["r"])
    assert 0 < lo < hi < 1


def test_unknown_market_returns_none():
    assert npx.project([1, 2, 3], "Faceoffs") is None


def test_saves_is_a_goalie_market():
    assert npx.canonical_market("Goalie Saves") == "saves"
    assert npx.canonical_market("Total Saves") == "saves"
    assert npx.MARKETS["saves"]["role"] == "goalie"
    proj = npx.project([28, 31, 22, 40, 25, 33], "saves")
    assert proj.market == "saves"
    assert 22 < proj.proj < 40
    # a ~26-save goalie is well over 20.5 and well under 40.5
    assert npx.prob_over(proj.proj, 20.5, proj.r) > npx.prob_over(proj.proj, 40.5, proj.r)


def test_roles_partition_markets():
    skater = [m for m, c in npx.MARKETS.items() if c["role"] == "skater"]
    goalie = [m for m, c in npx.MARKETS.items() if c["role"] == "goalie"]
    assert "saves" in goalie and "shots" in skater
    assert set(skater) & set(goalie) == set()


def _def_rows():
    # league allows 3.0 shots/appearance; TIGHT allows 2.0, LOOSE 4.0. Goalie
    # saves keyed the same way: SHOOTS_LOTS induces 34 saves vs league 26.
    rows = []
    for _ in range(200):
        rows.append({"team": "TIGHT", "shots": 2.0})
        rows.append({"team": "LOOSE", "shots": 4.0})
        rows.append({"team": "AVG", "shots": 3.0})
        rows.append({"team": "SHOOTS_LOTS", "saves": 34.0})
        rows.append({"team": "AVG", "saves": 26.0})
    return rows


def test_opponent_factor_uses_nhl_markets():
    tbl = npx.defense_factors(_def_rows())
    tight = npx.opponent_factor("Shots On Goal", "TIGHT", tbl)
    loose = npx.opponent_factor("SOG", "LOOSE", tbl)
    assert tight < 1.0 < loose                         # defense direction
    # saves ride the SHOOTING team's volume (right direction for a goalie)
    assert npx.opponent_factor("Goalie Saves", "SHOOTS_LOTS", tbl) > 1.0
    # unknown team / market -> neutral
    assert npx.opponent_factor("Shots", "NOPE", tbl) == 1.0
    assert npx.opponent_factor("Faceoffs", "TIGHT", tbl) == 1.0
