"""Regression tests for attach_generic_game_edges.

The totals branch used to index the SPORTS registry with the Sport dataclass
instead of its key (``SPORTS[sport].market_shrink``), which raised TypeError on
the first game with a matched totals offer; the blanket handler in _run_generic
then served the entire slate with no moneyline/total/spread EV at all (audit
2026-07 #1). These tests exercise the branch with a stubbed offers frame — the
resilience tests monkeypatch the whole function away, so nothing else covers it.
"""

import pandas as pd
import pytest

from project547 import pipeline
from project547.models import generic
from project547.sports import SPORTS


@pytest.fixture()
def _stub_bp(monkeypatch):
    def offers_rows():
        return [
            {"event_id": 10, "selection": "Over", "participant": None,
             "line": 220.0, "odds": -110, "active": True},
            {"event_id": 10, "selection": "Under", "participant": None,
             "line": 220.0, "odds": -110, "active": True},
        ]

    monkeypatch.setattr(pipeline.bettingpros, "game_market_ids",
                        lambda k: {"total": 2})
    monkeypatch.setattr(pipeline.bettingpros, "events",
                        lambda k, d: [{"id": 10}])
    monkeypatch.setattr(pipeline.bettingpros, "offers",
                        lambda *a, **kw: object())
    monkeypatch.setattr(pipeline.bettingpros, "flatten_offers",
                        lambda raw: offers_rows())
    monkeypatch.setattr(pipeline, "_bp_event_teams",
                        lambda events: {10: ["Boston Celtics"]})


def test_totals_branch_attaches_edges(_stub_bp):
    sport = SPORTS["NBA"]
    proj = generic.project_game(sport, None, None)
    games = pd.DataFrame([{
        "home_team": "Boston Celtics", "away_team": "Miami Heat",
        "home_win_prob": proj.home_win_prob, "_proj": proj,
    }])
    out = pipeline.attach_generic_game_edges(games, "NBA", "2026-02-01")
    r = out.iloc[0]
    assert r["total_line"] == 220.0
    # the whole point of the regression: EVs must attach instead of the apply
    # blowing up and the slate silently degrading to model-only
    assert r["over_ev"] is not None and r["under_ev"] is not None
    # integer line -> the push mass must be threaded into the EV
    assert r["over_p_push"] is not None and float(r["over_p_push"]) > 0
    assert r["over_p_used"] is not None
