import pytest

from onesource import ai


def test_available_false_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    ready, reason = ai.available()
    assert ready is False
    assert reason  # a user-facing explanation is provided


def test_analyze_raises_when_unconfigured(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    with pytest.raises(RuntimeError):
        ai.analyze("# Brief\nSome edge.")


def test_user_content_appends_question():
    brief = "# Brief\nbody"
    assert ai._user_content(brief, None) == brief
    out = ai._user_content(brief, "Best bet?")
    assert out.startswith(brief) and "Analyst question: Best bet?" in out


def test_default_model_is_opus():
    # default unless OSP_AI_MODEL overrides it
    assert ai.MODEL.startswith("claude-")
