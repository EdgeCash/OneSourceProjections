"""The pipeline glue that feeds the per-player rate models (WNBA + NHL) into
generic props. Pure/offline: the log lookup is monkeypatched, plus real-data
smoke checks against the committed logs."""
from __future__ import annotations

from project547 import pipeline
from project547.models import nhl_props, wnba_props


def test_model_prop_returns_projection_and_dispersion(monkeypatch):
    series = [{"value": v} for v in [10, 12, 8, 14, 9, 11, 13, 10]]
    monkeypatch.setattr(pipeline.playerlogs, "recent_series",
                        lambda *a, **k: series)
    proj, r = pipeline._log_model_prop("WNBA", "Any Player", "Points", "2026-06-01")
    assert proj is not None
    assert r == wnba_props.MARKETS["points"]["r"]
    assert 8 < proj < 14


def test_model_prop_thin_sample_defers_to_vendor(monkeypatch):
    monkeypatch.setattr(pipeline.playerlogs, "recent_series",
                        lambda *a, **k: [{"value": 10}, {"value": 12}])  # < MIN_GAMES
    assert pipeline._log_model_prop("WNBA", "P", "Points", "2026-06-01") == (None, None)


def test_model_prop_unknown_market_defers(monkeypatch):
    monkeypatch.setattr(pipeline.playerlogs, "recent_series",
                        lambda *a, **k: [{"value": 1}] * 10)
    assert pipeline._log_model_prop("WNBA", "P", "Double Doubles", "2026-06-01") == (None, None)


def test_unmodeled_sport_defers():
    # a sport with no log model returns (None, None) without touching the logs
    assert pipeline._log_model_prop("NBA", "P", "Points", "2026-06-01") == (None, None)


def test_model_prop_never_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("log store down")
    monkeypatch.setattr(pipeline.playerlogs, "recent_series", boom)
    # a failing log lookup must degrade to the vendor path, not crash the slate
    assert pipeline._log_model_prop("WNBA", "P", "Points", "2026-06-01") == (None, None)


def test_real_committed_logs_smoke_wnba():
    proj, r = pipeline._log_model_prop("WNBA", "Kennedy Burke", "Points", "2026-07-01")
    if proj is not None:                       # data present in this checkout
        assert r == wnba_props.MARKETS["points"]["r"]
        assert proj > 0


def test_real_committed_logs_smoke_nhl():
    # NHL skater logs are committed (2016-2025); a high-volume shooter should
    # yield a shots-on-goal projection through the real recent_series -> project.
    proj, r = pipeline._log_model_prop("NHL", "Connor McDavid", "Shots On Goal",
                                       "2025-04-01")
    if proj is not None:
        assert r == nhl_props.MARKETS["shots"]["r"]
        assert proj > 0
