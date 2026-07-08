"""Tweaks: NaN-safe JSON, MLB lineup 'pending' note, dark-mode + scoreboard wiring."""
import json

from project547 import pipeline


def test_json_sanitize_replaces_nonfinite():
    src = {"a": float("nan"), "b": [1.0, float("inf"), float("-inf"), 2],
           "c": {"d": float("nan"), "e": "x"}, "f": 3}
    out = pipeline.json_sanitize(src)
    assert out == {"a": None, "b": [1.0, None, None, 2],
                   "c": {"d": None, "e": "x"}, "f": 3}
    # and it now serializes under strict JSON (no bare NaN)
    json.dumps(out, allow_nan=False)


def test_ss_lineups_pending_note_for_mlb_when_empty():
    from app import ui
    g = {"lineups": {"home": [], "away": []}, "home_team": "SF", "away_team": "TOR"}
    html = ui._ss_lineups("MLB", g)
    assert "Not yet posted" in html            # MLB: explain the timing
    assert ui._ss_lineups("NBA", g) == ""      # other sports: silent
    # populated -> real lineup rows, not the note
    g2 = {"lineups": {"home": ["A", "B"], "away": ["C"]}, "home_team": "SF",
          "away_team": "TOR", "player_ids": {}}
    assert "Not yet posted" not in ui._ss_lineups("MLB", g2)


def test_scoreboard_and_theme_wiring_in_build():
    import scripts.build_static as bs
    # both palettes are emitted so a toggle can swap them
    from app import theme
    css = theme.theme_css_both()
    assert ':root[data-theme="dark"]' in css and ':root[data-theme="light"]' in css
    assert "#000000" in css     # classic-black dark bg
    # scoreboard league map excludes tennis (no team scoreboard shape)
    assert "ATP" not in bs.SB_LEAGUE_PATHS and bs.SB_LEAGUE_PATHS["MLB"] == "baseball/mlb"
