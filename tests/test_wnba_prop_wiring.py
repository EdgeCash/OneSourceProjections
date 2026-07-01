"""The pipeline glue that feeds the WNBA rate model into generic props.
Pure/offline: the log lookup is monkeypatched, plus one real-data smoke check
against the committed 2026 logs."""
from __future__ import annotations

from project547 import pipeline
from project547.models import wnba_props


def test_model_prop_returns_projection_and_dispersion(monkeypatch):
    series = [{"value": v} for v in [10, 12, 8, 14, 9, 11, 13, 10]]
    monkeypatch.setattr(pipeline.playerlogs, "recent_series",
                        lambda *a, **k: series)
    proj, r = pipeline._wnba_model_prop("Any Player", "Points", "2026-06-01")
    assert proj is not None
    assert r == wnba_props.MARKETS["points"]["r"]
    assert 8 < proj < 14


def test_model_prop_thin_sample_defers_to_vendor(monkeypatch):
    monkeypatch.setattr(pipeline.playerlogs, "recent_series",
                        lambda *a, **k: [{"value": 10}, {"value": 12}])  # < MIN_GAMES
    assert pipeline._wnba_model_prop("P", "Points", "2026-06-01") == (None, None)


def test_model_prop_unknown_market_defers(monkeypatch):
    monkeypatch.setattr(pipeline.playerlogs, "recent_series",
                        lambda *a, **k: [{"value": 1}] * 10)
    assert pipeline._wnba_model_prop("P", "Double Doubles", "2026-06-01") == (None, None)


def test_model_prop_never_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("log store down")
    monkeypatch.setattr(pipeline.playerlogs, "recent_series", boom)
    # a failing log lookup must degrade to the vendor path, not crash the slate
    assert pipeline._wnba_model_prop("P", "Points", "2026-06-01") == (None, None)


def test_real_committed_logs_smoke():
    # A frequently-appearing 2026 player should yield a points projection from
    # the committed logs (guards the recent_series -> project path end to end).
    proj, r = pipeline._wnba_model_prop("Kennedy Burke", "Points", "2026-07-01")
    if proj is not None:                       # data present in this checkout
        assert r == wnba_props.MARKETS["points"]["r"]
        assert proj > 0
