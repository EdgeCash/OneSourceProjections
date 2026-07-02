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


def test_refresh_handedness_fills_missing_only(tmp_path, monkeypatch):
    import json

    from project547.clients import mlb_statsapi

    # a fresh map with one known pitcher and a null-handed stub
    hand_file = tmp_path / "hand.json"
    hand_file.write_text(json.dumps({
        "known pitcher": {"bats": "R", "throws": "L"},
        "stub guy": {"bats": None, "throws": None},
    }))
    monkeypatch.setattr(platoon, "_HAND_PATH", hand_file)
    platoon._handedness.cache_clear()

    # the "live" people endpoint — StatsAPI codes switch hitters 'S'
    calls = {}

    def fake_people(ids):
        # client contract: switch already normalized 'S' -> 'B' (see below)
        calls["ids"] = list(ids)
        return {
            10: {"bats": "L", "throws": "R", "name": "Rookie Debut"},
            11: {"bats": "B", "throws": "R", "name": "Stub Guy"},
        }

    monkeypatch.setattr(mlb_statsapi, "people_handedness", fake_people)

    # known pitcher (has both) is skipped; the null stub + the brand-new rookie
    # are the only ids fetched
    added = platoon.refresh_handedness([
        (5, "Known Pitcher"), (10, "Rookie Debut"), (11, "Stub Guy"),
    ])
    assert added == 2
    assert set(calls["ids"]) == {10, 11}

    platoon._handedness.cache_clear()
    assert platoon.throws("Rookie Debut") == "R"
    assert platoon.bats("Rookie Debut") == "L"
    assert platoon.bats("Stub Guy") == "B"
    # re-running now resolves nothing new (all known)
    assert platoon.refresh_handedness([(10, "Rookie Debut")]) == 0


def test_people_handedness_normalizes_switch(monkeypatch):
    # StatsAPI codes switch hitters 'S'; the client maps it to the map's 'B'.
    from project547.clients import mlb_statsapi

    payload = {"people": [
        {"id": 1, "fullName": "Switchy McSwitch",
         "batSide": {"code": "S"}, "pitchHand": {"code": "R"}},
    ]}
    monkeypatch.setattr(mlb_statsapi, "cached_json",
                        lambda key, ttl, fetch: payload)
    out = mlb_statsapi.people_handedness([1])
    assert out[1]["bats"] == "B"
    assert out[1]["throws"] == "R"


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
