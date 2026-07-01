"""Tests for play_tier (the single confidence source of truth) and the
reusable play_card_html. Imports app.ui directly — the module import is
altair-free (only calibration_chart imports altair lazily), so these stay
out of test_ui.py which transitively pulls altair."""

import math

from app import ui


# ---------------------------------------------------------------------------
# play_tier — the band ladder (PASS / LEAN / CORE / WATCH / VERIFY)
# ---------------------------------------------------------------------------

def test_play_tier_pass_below_min_edge():
    t = ui.play_tier(0.01)            # below default 0.02 floor
    assert t["kind"] == "pass"
    assert t["label"] == "PASS"
    assert t["color"] == "var(--faint)"
    # the letter grade still rides along
    assert t["letter"] == ui._letter(0.01)


def test_play_tier_core_band():
    for ev in (0.02, 0.03, 0.0599):
        t = ui.play_tier(ev)
        assert t["kind"] == "core", ev
        assert t["label"] == "CORE PLAY"
        assert t["color"] == "var(--good)"


def test_play_tier_watch_band():
    for ev in (0.06, 0.07, 0.0799):
        t = ui.play_tier(ev)
        assert t["kind"] == "watch", ev
        assert t["label"] == "WATCH"
        assert t["color"] == "var(--mid)"


def test_play_tier_verify_huge_edge_is_a_warning():
    for ev in (0.08, 0.15, 0.40):
        t = ui.play_tier(ev)
        assert t["kind"] == "verify", ev
        assert t["label"] == "VERIFY"
        assert t["color"] == "var(--neg)"


def test_play_tier_lean_band_only_when_min_edge_below_core():
    # a custom bar under the 2% core floor opens the LEAN sliver
    t = ui.play_tier(0.015, min_edge=0.01)
    assert t["kind"] == "lean"
    assert t["label"] == "LEAN"
    assert t["color"] == "var(--mid)"
    # at/above the core floor it's a core play even with the low bar
    assert ui.play_tier(0.02, min_edge=0.01)["kind"] == "core"
    # with the default bar there is no lean band — sub-2% is just a pass
    assert ui.play_tier(0.015)["kind"] == "pass"


def test_play_tier_boundaries_are_half_open():
    # exact band edges land in the upper band (>= is inclusive)
    assert ui.play_tier(0.02)["kind"] == "core"
    assert ui.play_tier(0.06)["kind"] == "watch"
    assert ui.play_tier(0.08)["kind"] == "verify"


def test_play_tier_none_and_nan():
    for bad in (None, float("nan")):
        t = ui.play_tier(bad)
        assert t["kind"] == "pass"
        assert t["letter"] == "—"


def test_grade_still_returns_letter_and_color():
    # the refactor preserves the public _grade (letter, color) contract
    assert ui._grade(0.10) == ("A", "var(--good)")
    assert ui._grade(0.04) == ("C", "var(--mid)")
    assert ui._grade(-0.05) == ("F", "var(--neg)")
    assert ui._grade(None) == ("—", "var(--faint)")


# ---------------------------------------------------------------------------
# play_card_html — builds a non-empty themed string, never raises
# ---------------------------------------------------------------------------

_PLAY = {
    "sport": "MLB", "game": "Yankees @ Red Sox", "time": "2026-06-13T23:10:00Z",
    "bet": "Red Sox ML", "price": -120, "model_prob": 0.61, "ev": 0.04,
    "kelly": 0.8,
}


def test_play_card_builds_for_typical_play():
    html = ui.play_card_html(_PLAY)
    assert isinstance(html, str) and len(html) > 0
    # hero pick + price, the 4-cell grid, and the tier chip all present
    assert "Red Sox ML" in html
    assert "-120" in html
    assert "MARKET" in html and "IMPLIED" in html and "EDGE" in html
    assert "CORE PLAY" in html          # ev 0.04 -> core band
    # one-line WHY in the loosely-held numeric voice
    assert "Model 61%" in html and "market implies" in html


def test_play_card_graded_shows_clv_and_result():
    graded = {**_PLAY, "clv": 0.012, "won": True, "pnl": 0.8}
    html = ui.play_card_html(graded, graded=True)
    assert "CLV" in html
    assert "+1.2%" in html
    assert "✅ Win" in html
    assert "+0.8u" in html

    loss = {**_PLAY, "clv": -0.004, "won": False, "pnl": -1.0}
    html2 = ui.play_card_html(loss, graded=True)
    assert "-0.4%" in html2
    assert "❌ Loss" in html2
    assert "-1.0u" in html2


def test_play_card_tolerates_empty_and_partial_dicts():
    # empty dict must not raise and still returns a card shell
    for arg in ({}, None):
        html = ui.play_card_html(arg)
        assert isinstance(html, str) and len(html) > 0
    # partial: only a pick, no price/ev/prob
    partial = ui.play_card_html({"bet": "Some Team ML"})
    assert "Some Team ML" in partial
    assert "—" in partial               # blanks degrade gracefully
    # graded=True on a partial dict (no clv/won) also stays quiet, no raise
    assert isinstance(ui.play_card_html({"bet": "X"}, graded=True), str)


def test_play_card_edge_sign_coloring():
    pos = ui.play_card_html({**_PLAY, "ev": 0.05})
    neg = ui.play_card_html({**_PLAY, "ev": -0.05})
    assert "var(--good)" in pos
    assert "var(--neg)" in neg
