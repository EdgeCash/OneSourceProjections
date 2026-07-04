"""Smoke tests for the Edge Card static-site generator."""
import json
import math
from pathlib import Path

import pytest

from web import data as D

ROOT = Path(__file__).resolve().parent.parent
LATEST = ROOT / "data/output/latest.json"


def test_denan_sanitizes():
    dirty = {"a": float("nan"), "b": [1.0, float("inf")], "c": {"d": 2.5}}
    clean = D._denan(dirty)
    assert clean["a"] is None
    assert clean["b"] == [1.0, None]
    assert clean["c"]["d"] == 2.5


@pytest.mark.skipif(not LATEST.exists(), reason="no latest.json in this env")
def test_load_latest_has_no_nan():
    blob = D.load_latest(str(LATEST))
    # walk the tree; there must be no NaN/Inf floats left
    stack = [blob]
    while stack:
        v = stack.pop()
        if isinstance(v, float):
            assert not (math.isnan(v) or math.isinf(v))
        elif isinstance(v, dict):
            stack.extend(v.values())
        elif isinstance(v, list):
            stack.extend(v)
    # re-serialisable as strict JSON (would raise on NaN)
    json.dumps(blob, allow_nan=False)


@pytest.mark.skipif(not LATEST.exists(), reason="no latest.json in this env")
def test_build_writes_core_pages(tmp_path):
    from scripts import build_site
    out = tmp_path / "site"
    w = build_site.build(LATEST, out)
    assert w["pages"] > 0
    for name in ("index.html", "mlb.html", "props.html", "performance.html",
                 "assets/app.css", "assets/app.js"):
        assert (out / name).exists(), f"missing {name}"
    # at least one MLB edge card, and it embeds the Copy-for-AI markdown
    cards = list(out.glob("edge-card-mlb-*.html"))
    assert cards, "no MLB edge cards generated"
    assert "id='ai-markdown'" in cards[0].read_text()


@pytest.mark.skipif(not LATEST.exists(), reason="no latest.json in this env")
def test_no_crash_on_missing_pitcher():
    """A TBD/NaN starter must not blow up sheet_data (regression)."""
    g = {"away_team": "A", "home_team": "B", "away_pitcher": float("nan"),
         "home_pitcher": None, "away_win_prob": 0.5, "home_win_prob": 0.5}
    d = D.sheet_data("MLB", g, {}, "2026-07-03")
    assert isinstance(d, dict)
