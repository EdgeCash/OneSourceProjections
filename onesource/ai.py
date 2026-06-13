"""In-app AI analyst — turn a research brief into a written read with Claude.

The dashboard already exports clean markdown briefs (``app.ui.ai_brief_*``).
This module feeds one to Claude and streams back an analysis, so you get an
instant, grounded second opinion *inside* the app instead of copy-pasting into
a chatbot — the research showed every elite workflow ends in "screenshot → AI",
so we build that in.

Degrades gracefully: if the ``anthropic`` SDK isn't installed or no API key is
configured, ``available()`` is ``False`` and the dashboard falls back to the
copy-paste brief. Uses Claude Opus 4.8 with adaptive thinking by default
(override the model with ``OSP_AI_MODEL``).
"""

from __future__ import annotations

import os

# Default to the most capable Opus-tier model; let the user pin another via env.
MODEL = os.environ.get("OSP_AI_MODEL", "claude-opus-4-8")

SYSTEM = (
    "You are a sharp, disciplined sports-betting analyst embedded in a personal "
    "projections dashboard. You receive a markdown brief with a quantitative "
    "model's projections, edges (EV vs market), hit-rate splits, and context "
    "for a game, a player prop, or a whole slate of bets.\n\n"
    "Deliver a concise, honest read:\n"
    "1. Lead with the single best play (or an explicit 'pass') and the number "
    "that justifies it.\n"
    "2. Call out where the model and the market disagree, and say which you'd "
    "trust and why.\n"
    "3. Flag risks: injuries, small samples, stale lines, and implausibly large "
    "edges (a >15% edge usually means the model is missing news, not that the "
    "market is wrong).\n"
    "4. Note staking discipline — the brief sizes at quarter-Kelly.\n\n"
    "Be specific and quantitative, and never invent facts that aren't in the "
    "brief. This is personal research, not financial advice."
)


def available() -> tuple[bool, str]:
    """``(ready, reason)``. Ready only when the SDK is importable *and* a key is
    configured. ``reason`` is a user-facing message when not ready."""
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False, "Install the `anthropic` package to enable the AI analyst."
    if not (os.environ.get("ANTHROPIC_API_KEY")
            or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        return False, ("Set ANTHROPIC_API_KEY (env var or Streamlit secret) to "
                       "enable the AI analyst.")
    return True, ""


def _user_content(brief: str, question: str | None) -> str:
    if question and question.strip():
        return f"{brief}\n\n---\nAnalyst question: {question.strip()}"
    return brief


def analyze_stream(brief: str, question: str | None = None,
                   model: str | None = None):
    """Yield the analyst's response text chunk-by-chunk (for ``st.write_stream``).
    Raises ``RuntimeError`` with a friendly message if not configured."""
    ready, reason = available()
    if not ready:
        raise RuntimeError(reason)
    import anthropic

    client = anthropic.Anthropic()
    with client.messages.stream(
        model=model or MODEL,
        max_tokens=6000,
        system=SYSTEM,
        thinking={"type": "adaptive"},
        output_config={"effort": "medium"},
        messages=[{"role": "user", "content": _user_content(brief, question)}],
    ) as stream:
        yield from stream.text_stream


def analyze(brief: str, question: str | None = None,
            model: str | None = None) -> str:
    """Blocking variant: return the full analysis as a string."""
    return "".join(analyze_stream(brief, question, model)).strip()
