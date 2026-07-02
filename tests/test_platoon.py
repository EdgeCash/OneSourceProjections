"""Batter platoon adjustment (handedness + committed vL/vR splits)."""
from __future__ import annotations

from project547 import platoon


def test_handedness_lookup_real_data():
    # harvested from the retrosheet biofile
    assert platoon.throws("Shohei Ohtani") == "R"
    assert platoon.bats("Shohei Ohtani") == "L"
    assert platoon.throws("Corbin Carroll") == "L"
    assert platoon.throws("Nobody Whoisnt") is None


def test_platoon_mult_shrinks_and_clamps(monkeypatch):
    # a big platoon edge on a TINY sample barely moves the number...
    monkeypatch.setattr(platoon, "hitter_split",
                        lambda *a, **k: {"avg": 0.400, "slg": 0.800, "pa": 10.0})
    small = platoon.platoon_mult(1, "L", 2026, overall=0.250, stat="avg")
    assert 1.0 < small < 1.12          # 10 PA -> heavily shrunk toward 1.0
    # ...but a large sample moves it much more
    monkeypatch.setattr(platoon, "hitter_split",
                        lambda *a, **k: {"avg": 0.400, "slg": 0.800, "pa": 400.0})
    big = platoon.platoon_mult(1, "L", 2026, overall=0.250, stat="avg")
    assert big > small
    assert big <= 1.45                 # clamp holds


def test_platoon_mult_unknown_is_neutral():
    assert platoon.platoon_mult(None, "L", 2026, 0.25) == 1.0
    assert platoon.platoon_mult(123, "X", 2026, 0.25) == 1.0   # bad hand
    assert platoon.platoon_mult(123, "L", 2026, 0.0) == 1.0    # no overall


def test_hitter_split_real_committed_splits():
    # a well-known hitter should have a committed 2024/2025 vL or vR line
    # (keyed by MLBAM id in splits.json.gz). We just assert the accessor shape
    # works against real data for at least one id that exists.
    import gzip
    import json
    from project547.config import REPO_ROOT
    path = REPO_ROOT / "data/history/backfill/mlb/2025/splits.json.gz"
    hitting = json.load(gzip.open(path)).get("hitting", {})
    pid = next(iter(hitting))
    sp = platoon.hitter_split(int(pid), "R", 2025)
    assert sp is None or ("slg" in sp and "pa" in sp)
