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
