"""Offline tests for projection tracking (pure join/scoring + dedup)."""

from __future__ import annotations

import json

from project547 import tracking


def _projs():
    return [
        # e1: home won — model & market & BP all correct
        {"date": "2026-06-24", "event_id": "e1", "source": "model", "home_wp": 0.62},
        {"date": "2026-06-24", "event_id": "e1", "source": "market", "home_wp": 0.55},
        {"date": "2026-06-24", "event_id": "e1", "source": "bettingpros", "home_wp": 0.58},
        # e2: home lost — model right (favors away), market WRONG (favors home)
        {"date": "2026-06-24", "event_id": "e2", "source": "model", "home_wp": 0.40},
        {"date": "2026-06-24", "event_id": "e2", "source": "market", "home_wp": 0.55},
        # e3: no outcome yet -> ignored
        {"date": "2026-06-24", "event_id": "e3", "source": "model", "home_wp": 0.7},
    ]


def _outs():
    return [{"date": "2026-06-24", "event_id": "e1", "home_won": 1},
            {"date": "2026-06-24", "event_id": "e2", "home_won": 0}]


def test_summary_per_source_accuracy_and_lift():
    s = tracking.summary(projections=_projs(), outcomes=_outs())
    bs = s["by_source"]
    assert s["graded_events"] == 2
    # model: e1 (0.62,won) correct, e2 (0.40,lost) correct -> 2/2
    assert bs["model"]["n"] == 2 and bs["model"]["accuracy"] == 1.0
    # market: e1 correct, e2 (0.55 -> predicts home, home lost) wrong -> 1/2
    assert bs["market"]["accuracy"] == 0.5
    # lift: model beats market by +0.5 on accuracy
    assert bs["model"]["lift_vs_market"] == 0.5
    # brier is computed
    assert bs["model"]["brier"] is not None


def test_summary_ignores_ungraded_and_filters_dates():
    s = tracking.summary(projections=_projs(), outcomes=_outs(),
                         dates="2026-06-24")
    assert s["by_source"]["model"]["n"] == 2     # e3 has no outcome -> excluded
    empty = tracking.summary(projections=_projs(), outcomes=_outs(),
                             dates="2099-01-01")
    assert empty["by_source"] == {}


def test_append_dedupes(tmp_path, monkeypatch):
    p = tmp_path / "projections.jsonl"
    monkeypatch.setattr(tracking, "PROJ", p)
    r = {"date": "d", "sport": "MLB", "event_id": 5, "game": "A @ B",
         "source": "model", "home_wp": 0.6}
    assert tracking.log_projection(**{k: r[k] for k in
        ("date", "sport", "event_id", "game", "source")}, home_wp=0.6) == 1
    # same (date,event,source) again -> no new row
    assert tracking.log_projection("d", "MLB", 5, "A @ B", "model", 0.6) == 0
    # different source -> appends
    assert tracking.log_projection("d", "MLB", 5, "A @ B", "market", 0.55) == 1
    assert len(p.read_text().strip().splitlines()) == 2


def test_record_outcome_dedupes(tmp_path, monkeypatch):
    o = tmp_path / "outcomes.jsonl"
    monkeypatch.setattr(tracking, "OUTC", o)
    assert tracking.record_outcome("d", 5, 1, 5, 3) == 1
    assert tracking.record_outcome("d", 5, 1) == 0
    assert json.loads(o.read_text().splitlines()[0])["home_won"] == 1
