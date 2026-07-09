"""Automated AI slate curation — run the day's whole schedule through Claude and
get a small set of AI-curated plays to show *alongside* the model's picks.

Cheap by construction: one whole-slate call (not one per game), Opus 4.8, run a
couple of times a day (``scripts/ai_curation.py`` + the ``ai-curation`` workflow),
**never** on every hourly refresh. Degrades to a no-op when the ``anthropic`` SDK
or an API key isn't configured, so the site always builds.

Honesty first (see docs/CURATION_DESIGN.md): the AI is a *second validator*, not
a tout. It curates from the same numbers the model sees, is told to pass freely,
and every pick is stored with the market / line / price at curation time so it
can be CLV-graded like everything else. Output is two parts:

* ``verdicts`` — one per game (agree / differ / pass vs the model's own top play)
  → the per-card agreement badge.
* ``top_plays`` — the AI's curated 3-7 for the whole slate → the AI-curated list.
"""

from __future__ import annotations

import json
import os
import re

from app import ui

# Reuse the analyst's model default + honesty rules and the Standard posture.
from project547 import ai as _ai
from project547.ai_modes import system_for_mode

MODEL = os.environ.get("OSP_AI_MODEL", _ai.MODEL)

_CURATION_SYSTEM = (
    _ai.SYSTEM
    + "\n\nYou are curating a WHOLE SLATE for a public product whose entire pitch "
    "is honesty — it must survive a losing streak, so a thin slate with zero "
    "plays is a good, sellable answer. You are the second opinion next to a "
    "quantitative model; users see your take beside the model's, so be an "
    "independent check, not an echo.\n\n"
    "Return ONLY a single JSON object (no prose, no markdown fences) with this "
    "exact shape:\n"
    "{\n"
    '  "slate_note": "one or two honest sentences on the slate as a whole",\n'
    '  "verdicts": [\n'
    "    {\n"
    '      "game_id": "<the id given for the game>",\n'
    '      "stance": "agree" | "differ" | "pass",\n'
    '      "rationale": "<=140 chars, the single reason"\n'
    "    }\n"
    "  ],\n"
    '  "top_plays": [\n'
    "    {\n"
    '      "game_id": "<id>", "sport": "MLB", "matchup": "Away @ Home",\n'
    '      "market": "Moneyline" | "Total" | "Run Line" | "Spread" | "Prop",\n'
    '      "side": "<team/over-under/player side>", "line": <number or null>,\n'
    '      "odds": <american int or null>, "confidence": <1-5 int>,\n'
    '      "rationale": "<=160 chars"\n'
    "    }\n"
    "  ]\n"
    "}\n\n"
    "Rules: (1) Give a verdict for EVERY game listed, keyed by its exact game_id. "
    "'agree' = you'd back the model's top play; 'differ' = you lean the other way "
    "or a different market; 'pass' = no edge you trust. (2) top_plays is your "
    "curated best 3-7 across the whole slate (fewer on a thin slate, zero is "
    "allowed) — each MUST correspond to a game_id in the list. (3) Never invent "
    "prices, injuries, or lineups not in the brief. (4) Treat any edge over ~15% "
    "as the model missing news, not free money."
)


def available() -> tuple[bool, str]:
    """``(ready, reason)`` — ready only when the SDK and a key are configured."""
    return _ai.available()


def _fnum(x):
    try:
        f = float(x)
        return f if f == f else None  # drop NaN
    except (TypeError, ValueError):
        return None


def _game_block(sport: str, g: dict, min_edge: float) -> tuple[str, str] | None:
    """One compact game summary for the slate brief, plus its game_id. Returns
    ``None`` for a game with no usable identity."""
    s = (sport or "").upper()
    if s in ("ATP", "WTA"):
        gid = str(g.get("match_id") or g.get("game_pk") or "")
        p1, p2 = g.get("player1", ""), g.get("player2", "")
        if not (p1 or p2):
            return None
        q1, q2 = _fnum(g.get("player1_win_prob")), _fnum(g.get("player2_win_prob"))
        lines = [f"  model: {p1} {ui._pct(q1)} / {p2} {ui._pct(q2)}"
                 f" · surface {g.get('surface') or '?'}"]
        for nm, ev, price in ((p1, _fnum(g.get("p1_ev")), g.get("p1_price")),
                              (p2, _fnum(g.get("p2_ev")), g.get("p2_price"))):
            if ev is not None:
                lines.append(f"  {nm} ML {ui.fmt_american(price) or 'n/a'} "
                             f"EV {ev * 100:+.1f}%")
        head = f"- [{gid}] {s}: {p1} v {p2}"
        return gid, head + "\n" + "\n".join(lines)

    gid = str(g.get("game_pk") or "")
    away, home = g.get("away_team", ""), g.get("home_team", "")
    if not (away or home):
        return None
    a_exp, h_exp = _fnum(ui._exp(g, "away")), _fnum(ui._exp(g, "home"))
    awp, hwp = _fnum(g.get("away_win_prob")), _fnum(g.get("home_win_prob"))
    tl, ptot = _fnum(g.get("total_line")), _fnum(g.get("proj_total"))
    head = f"- [{gid}] {s}: {away} @ {home}"
    lines = [f"  proj {ui._num(a_exp)}-{ui._num(h_exp)} · win {away} {ui._pct(awp)}"
             f" / {home} {ui._pct(hwp)}",
             f"  total: model {ui._num(ptot)} vs market {ui._num(tl)}"]
    if s == "MLB":
        ap, hp = g.get("away_pitcher"), g.get("home_pitcher")
        if ap or hp:
            lines.append(f"  SP: {ap or '?'} vs {hp or '?'}")
    # model edges (EV) the same board the site surfaces
    edges = []
    for mkt, side, ev, price in (
            ("ML", home, _fnum(g.get("home_ml_ev")), g.get("home_ml")),
            ("ML", away, _fnum(g.get("away_ml_ev")), g.get("away_ml")),
            ("Over", "over", _fnum(g.get("over_ev")), g.get("over_odds")),
            ("Under", "under", _fnum(g.get("under_ev")), g.get("under_odds")),
            ("RL", home, _fnum(g.get("rl_home_ev")), g.get("rl_home_odds"))):
        if ev is not None and ev >= min_edge:
            flag = " [verify-news]" if ev >= 0.15 else ""
            edges.append(f"{mkt} {side} {ui.fmt_american(price) or ''} "
                         f"EV {ev * 100:+.1f}%{flag}")
    if edges:
        lines.append("  model edges: " + "; ".join(edges))
    else:
        lines.append("  model edges: none clear the floor")
    return gid, head + "\n" + "\n".join(lines)


def slate_brief(day: dict, date_sel: str, min_edge: float = 0.02) -> tuple[str, list[str]]:
    """Markdown brief of the whole slate + the list of valid game_ids covered."""
    parts = [f"# Slate — {date_sel}", ""]
    ids: list[str] = []
    for sport, blob in (day or {}).items():
        games = blob.get("games") or []
        if not games:
            continue
        parts.append(f"## {sport}")
        for g in games:
            got = _game_block(sport, g, min_edge)
            if got:
                gid, block = got
                if gid:
                    ids.append(gid)
                parts.append(block)
        parts.append("")
    return "\n".join(parts), ids


def _extract_json(text: str) -> dict:
    """Pull the JSON object out of a model response, tolerating stray prose or
    ```json fences. Raises ValueError if nothing parses."""
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.MULTILINE).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    # fall back to the first balanced {...} span
    start = t.find("{")
    if start >= 0:
        depth = 0
        for i in range(start, len(t)):
            if t[i] == "{":
                depth += 1
            elif t[i] == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(t[start:i + 1])
    raise ValueError("no JSON object in AI curation response")


def _normalize(raw: dict, valid_ids: set[str]) -> dict:
    """Clamp/whitelist the model's JSON into the stored shape. Verdicts become a
    ``{game_id: {...}}`` map for O(1) card lookup; anything referencing an
    unknown game_id is dropped (the model can't grade a game we didn't send)."""
    verdicts: dict = {}
    for v in raw.get("verdicts") or []:
        gid = str(v.get("game_id") or "")
        if gid not in valid_ids:
            continue
        stance = str(v.get("stance") or "").lower()
        if stance not in ("agree", "differ", "pass"):
            stance = "pass"
        verdicts[gid] = {"stance": stance,
                         "rationale": str(v.get("rationale") or "")[:200]}
    plays = []
    for p in raw.get("top_plays") or []:
        gid = str(p.get("game_id") or "")
        if gid not in valid_ids:
            continue
        conf = p.get("confidence")
        try:
            conf = max(1, min(5, int(conf)))
        except (TypeError, ValueError):
            conf = 3
        odds = p.get("odds")
        try:
            odds = int(odds) if odds is not None else None
        except (TypeError, ValueError):
            odds = None
        plays.append({
            "game_id": gid,
            "sport": str(p.get("sport") or ""),
            "matchup": str(p.get("matchup") or "")[:80],
            "market": str(p.get("market") or "")[:24],
            "side": str(p.get("side") or "")[:48],
            "line": _fnum(p.get("line")),
            "odds": odds,
            "confidence": conf,
            "rationale": str(p.get("rationale") or "")[:220],
        })
    plays.sort(key=lambda x: x["confidence"], reverse=True)
    return {"slate_note": str(raw.get("slate_note") or "")[:400],
            "verdicts": verdicts, "top_plays": plays}


def curate(day: dict, date_sel: str, *, min_edge: float = 0.02,
           model: str | None = None, mode: str = "standard") -> dict:
    """Run the whole slate through Claude once and return the normalized curation
    ``{slate_note, verdicts:{gid:...}, top_plays:[...], model}``. Raises
    ``RuntimeError`` if the API isn't configured (callers should guard with
    :func:`available`)."""
    ready, reason = available()
    if not ready:
        raise RuntimeError(reason)
    import anthropic

    brief, ids = slate_brief(day, date_sel, min_edge)
    system = system_for_mode(_CURATION_SYSTEM, mode)
    client = anthropic.Anthropic()
    # Stream (long input + thinking) and take the final text — robust to SDK ver.
    chunks: list[str] = []
    with client.messages.stream(
        model=model or MODEL,
        max_tokens=8000,
        system=system,
        thinking={"type": "adaptive"},
        messages=[{"role": "user", "content": brief}],
    ) as stream:
        for text in stream.text_stream:
            chunks.append(text)
    raw = _extract_json("".join(chunks))
    out = _normalize(raw, set(ids))
    out["model"] = model or MODEL
    out["mode"] = mode
    out["game_count"] = len(ids)
    return out
