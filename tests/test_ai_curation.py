import json

import pytest

from project547 import ai_curation


def _day():
    return {
        "MLB": {"games": [
            {"game_pk": 111, "away_team": "Away A", "home_team": "Home A",
             "away_win_prob": 0.44, "home_win_prob": 0.56,
             "home_ml": -140, "away_ml": 130, "home_ml_ev": 0.05, "away_ml_ev": -0.02,
             "total_line": 8.5, "proj_total": 9.1, "over_odds": -105, "over_ev": 0.06,
             "away_pitcher": "Ace A", "home_pitcher": "Ace B"},
            {"game_pk": 222, "away_team": "Away B", "home_team": "Home B",
             "away_win_prob": 0.51, "home_win_prob": 0.49, "total_line": 7.0,
             "proj_total": 7.1},
        ]},
        "ATP": {"games": [
            {"match_id": "t1", "player1": "P One", "player2": "P Two",
             "player1_win_prob": 0.6, "player2_win_prob": 0.4, "surface": "hard",
             "p1_ev": 0.03, "p1_price": -120},
        ]},
    }


def test_available_delegates(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    ready, reason = ai_curation.available()
    assert ready is False and reason


def test_curate_raises_when_unconfigured(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    with pytest.raises(RuntimeError):
        ai_curation.curate(_day(), "2026-07-08")


def test_slate_brief_lists_every_game_id():
    brief, ids = ai_curation.slate_brief(_day(), "2026-07-08")
    assert set(ids) == {"111", "222", "t1"}
    assert "MLB" in brief and "ATP" in brief
    assert "Ace A" in brief  # pitchers surfaced
    assert "model edges" in brief


def test_extract_json_tolerates_fences_and_prose():
    payload = {"slate_note": "x", "verdicts": [], "top_plays": []}
    fenced = "Here you go:\n```json\n" + json.dumps(payload) + "\n```\nthanks"
    assert ai_curation._extract_json(fenced)["slate_note"] == "x"
    bare = json.dumps(payload)
    assert ai_curation._extract_json(bare) == payload


def test_extract_json_raises_when_absent():
    with pytest.raises(ValueError):
        ai_curation._extract_json("no json here at all")


def test_normalize_whitelists_ids_and_clamps():
    raw = {
        "slate_note": "note",
        "verdicts": [
            {"game_id": "111", "stance": "agree", "rationale": "ok"},
            {"game_id": "999", "stance": "agree", "rationale": "ghost game"},  # dropped
            {"game_id": "222", "stance": "bogus", "rationale": "bad stance"},  # -> pass
        ],
        "top_plays": [
            {"game_id": "111", "sport": "MLB", "matchup": "A @ B",
             "market": "Moneyline", "side": "Home A", "odds": "-140",
             "confidence": 9, "rationale": "r"},  # confidence clamped to 5
            {"game_id": "404", "sport": "MLB", "side": "Nope", "confidence": 3},  # dropped
        ],
    }
    out = ai_curation._normalize(raw, {"111", "222"})
    assert set(out["verdicts"]) == {"111", "222"}
    assert out["verdicts"]["222"]["stance"] == "pass"
    assert len(out["top_plays"]) == 1
    play = out["top_plays"][0]
    assert play["game_id"] == "111" and play["confidence"] == 5 and play["odds"] == -140


def test_curate_parses_streamed_json(monkeypatch):
    """A fake anthropic client whose stream yields a JSON blob in chunks — curate
    should assemble, parse, normalize, and tag it with the model."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    payload = {
        "slate_note": "thin board",
        "verdicts": [{"game_id": "111", "stance": "differ", "rationale": "fade"}],
        "top_plays": [{"game_id": "222", "sport": "MLB", "matchup": "Away B @ Home B",
                       "market": "Total", "side": "Under", "line": 7.0, "odds": -110,
                       "confidence": 4, "rationale": "pitchers' duel"}],
    }
    text = json.dumps(payload)

    class _Stream:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        @property
        def text_stream(self):
            # emit in two chunks to exercise the accumulator
            yield text[: len(text) // 2]
            yield text[len(text) // 2:]

    class _Messages:
        def stream(self, **kw):
            assert kw["model"].startswith("claude-")
            assert kw["thinking"] == {"type": "adaptive"}
            return _Stream()

    class _Client:
        messages = _Messages()

    fake_anthropic = type("m", (), {"Anthropic": staticmethod(lambda: _Client())})
    monkeypatch.setitem(__import__("sys").modules, "anthropic", fake_anthropic)

    out = ai_curation.curate(_day(), "2026-07-08")
    assert out["model"].startswith("claude-")
    assert out["game_count"] == 3
    assert out["verdicts"]["111"]["stance"] == "differ"
    assert out["top_plays"][0]["side"] == "Under"
