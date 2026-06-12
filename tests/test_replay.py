"""Tests for the data library (persistence + compaction) and replay mode."""

import gzip
import json

import pytest

from onesource import replay, snapshots
from onesource.clients import bettingpros


def test_flatten_offers_passthrough():
    pre = [{"participant": "Marlins", "odds": 118, "market_id": 122,
            "event_id": 1, "active": True, "line": 1, "selection": "Marlins",
            "book_id": 0, "is_best": False}]
    assert bettingpros.flatten_offers(pre) == pre
    # raw payloads still get flattened (not passed through)
    raw = [{"market_id": 122, "event_id": 1, "selections": []}]
    assert bettingpros.flatten_offers(raw) == []


def test_flatten_props_passthrough():
    pre = [{"participant": "X", "bp_line": 9.5, "over_odds": -110}]
    assert bettingpros.flatten_props(pre) == pre


def test_compact_gzips_old_files(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshots, "SNAP_DIR", tmp_path)
    d = tmp_path / "mlb"
    d.mkdir()
    (d / "2020-01-01.jsonl").write_text('{"a": 1}\n')
    (d / "2099-01-01.jsonl").write_text('{"b": 2}\n')   # future = keep raw
    n = snapshots.compact(keep_raw_days=1)
    assert n == 1
    assert not (d / "2020-01-01.jsonl").exists()
    with gzip.open(d / "2020-01-01.jsonl.gz", "rt") as f:
        assert json.loads(f.read()) == {"a": 1}
    assert (d / "2099-01-01.jsonl").exists()


def test_replay_reads_jsonl_and_gz(tmp_path, monkeypatch):
    monkeypatch.setattr(replay, "SNAP_DIR", tmp_path)
    d = tmp_path / "mlb"
    d.mkdir()
    rows = [
        {"kind": "game", "market_id": 122, "event_id": 5, "odds": -110,
         "participant": "Yankees", "captured_at": "2026-06-12T10:00:00"},
        {"kind": "game", "market_id": 122, "event_id": 5, "odds": -115,
         "participant": "Yankees", "captured_at": "2026-06-12T14:00:00"},
        {"kind": "prop", "market_id": 403, "bp_line": 1.5,
         "participant": "A", "captured_at": "2026-06-12T14:00:00"},
    ]
    with gzip.open(d / "2026-06-12.jsonl.gz", "wt") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    latest = replay._latest_rows("MLB", "2026-06-12")
    # only the last capture survives (the -115, not the -110)
    assert len(latest) == 2
    assert {r["odds"] for r in latest if r["kind"] == "game"} == {-115}


def test_replay_offers_and_events(tmp_path, monkeypatch):
    monkeypatch.setattr(replay, "SNAP_DIR", tmp_path)
    monkeypatch.setattr(replay, "MARKETS_DIR", tmp_path / "markets")
    d = tmp_path / "wnba"
    d.mkdir()
    (d / "2026-06-13.jsonl").write_text("\n".join(json.dumps(r) for r in [
        {"kind": "game", "market_id": 371, "event_id": 9, "odds": 120,
         "participant": "Aces", "captured_at": "t1"},
        {"kind": "prop", "market_id": 151, "bp_line": 22.5,
         "participant": "A'ja Wilson", "captured_at": "t1"},
    ]))
    replay.activate()
    replay.set_date("2026-06-13")
    from onesource.clients import bettingpros as bp
    assert len(bp.offers("WNBA", 371)) == 1
    assert bp.offers("WNBA", 999) == []          # other market filtered out
    assert len(bp.props("WNBA", "2026-06-13")) == 1
    evs = bp.events("WNBA", "2026-06-13")        # no events file -> synth ids
    assert evs == [{"id": 9}]
    assert bp.markets("WNBA") == []              # no catalog file -> empty


@pytest.fixture(autouse=True)
def _restore_clients():
    """replay.activate() rebinds module functions; restore them so other
    tests see the real clients."""
    import importlib

    from onesource.clients import bettingpros as bp
    from onesource.clients import fantasypros as fp
    saved_bp = {k: getattr(bp, k) for k in ("markets", "events", "offers", "props")}
    saved_fp = fp.mlb_projections
    yield
    for k, v in saved_bp.items():
        setattr(bp, k, v)
    fp.mlb_projections = saved_fp
