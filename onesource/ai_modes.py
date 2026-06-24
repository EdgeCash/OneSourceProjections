"""Ask-AI risk modes — Conservative / Standard / Aggressive.

Ported from the Edge Equation "styled curation prompts" (Sports-projections
`src/api/curation_styles.py`, commit 9ccb1918). The original gave the user three
copy-paste prompts that changed the model's *betting posture* — selectivity,
stake ladder, correlation policy, prop discipline, the edge floor, and how
strict the four-persona sign-off is — without changing the underlying numbers.

Here those same rule blocks (preserved close to verbatim) become a posture the
in-app AI analyst (`onesource/ai.py`) can adopt when you press Ask-AI on a game
breakdown: pick a mode and the analyst's recommendations get more or less
aggressive, anchored to the same projections/edges.
"""

from __future__ import annotations

# The four-persona sign-off, shared across modes (verbatim from the source).
_PERSONA_LINES = """\
     - The Quant: EV vs price?  __
     - The Capper: matchup / weather / park story?  __
     - The Money Manager: bankroll math + correlation OK?  __
     - The Skeptic: do we know something the market doesn't?  __"""

_RULES_STANDARD = f"""\
## Posture (STANDARD)

1. **Be selective.** Aim for 3-7 plays. Zero is a valid output on a thin slate.
2. **Sizing (hard rules, not vibes):**
     - 1.0u default. Use this unless the play clears the bars below.
     - 1.5u requires TWO independent confirmations
       (e.g. engine edge AND recent form OR matchup angle).
     - 2.0u maximum, requires THREE confirmations. Reserve for standouts.
3. **Anti-correlation hard cap:** ONE market per game maximum.
4. **Player-prop discipline:** only if (a) the player is in the lineup or the
   announced starter, AND (b) the engine edge is > 8%.
5. **Skip noisy markets:** edge under 3%, no pick.
6. **Persona check** (one line each):
{_PERSONA_LINES}
   If any vetos: drop the pick."""

_RULES_CONSERVATIVE = f"""\
## Posture (CONSERVATIVE)

1. **Be highly selective.** Aim for 2-4 plays. Zero is common and fully
   acceptable -- protecting the bankroll beats forcing action.
2. **Sizing (hard rules, smaller ladder):**
     - 0.5u default. Use this unless the play clears the bars below.
     - 1.0u requires TWO independent confirmations
       (engine edge AND recent form OR matchup angle).
     - 1.5u maximum, requires THREE confirmations AND unanimous persona
       sign-off. Reserve for the single best play on the board.
3. **Anti-correlation hard cap:** ONE market per game maximum, no exceptions.
   Prefer the lowest-variance market available.
4. **Player-prop discipline:** only if (a) the player is CONFIRMED in the
   posted lineup (never "announced/likely"), AND (b) the engine edge is > 12%.
5. **Skip noisy markets:** edge under 5%, no pick. Avoid plus-money dart throws.
6. **Lean on stable samples:** down-weight L5/L10 swings; require the
   season-long number to agree before sizing up.
7. **Persona check** (one line each):
{_PERSONA_LINES}
   ALL FOUR must sign off. Any veto OR any neutral: drop the pick."""

_RULES_AGGRESSIVE = f"""\
## Posture (AGGRESSIVE)

1. **Cast a wider net.** Aim for 5-10 plays. Every pick still needs a real
   edge -- volume is not an excuse for noise.
2. **Sizing (hard rules, higher ceiling):**
     - 1.0u default.
     - 1.5u requires ONE confirmation beyond the engine edge.
     - 2.0u requires TWO confirmations.
     - 3.0u maximum, requires THREE confirmations. Reserve for your single
       highest-conviction play.
3. **Correlation allowed, with eyes open:** up to TWO markets per game when
   they share a thesis; never exceed 2u combined exposure on one game.
4. **Player-prop aggression:** play props when (a) the player is in the lineup
   or announced starter, AND (b) the engine edge is > 5%.
5. **Lower noise floor:** edge under 2%, no pick (still skip true coinflips).
6. **Hunt what the market hasn't caught:** prioritise the larger model-vs-market
   gaps and plus-money spots where the engine and recent form agree. Lean into
   the 10-25% edges others fade -- but hard-skip anything that smells like a
   stale line or a data error."""

# key -> (display name, one-line description, accent hex, rules block)
MODES: dict[str, tuple[str, str, str, str]] = {
    "conservative": (
        "Conservative",
        "Capital-preservation: fewer plays, smaller ladder, unanimous sign-off.",
        "22c55e", _RULES_CONSERVATIVE,
    ),
    "standard": (
        "Standard",
        "House posture: selective, edge-anchored, one market per game.",
        "0ea5e9", _RULES_STANDARD,
    ),
    "aggressive": (
        "Aggressive",
        "Action mode: more plays and a higher ceiling, taking variance to chase "
        "model edges the market hasn't caught.",
        "f59e0b", _RULES_AGGRESSIVE,
    ),
}

MODE_ORDER = ("conservative", "standard", "aggressive")
DEFAULT_MODE = "standard"


def system_for_mode(base_system: str, mode: str | None) -> str:
    """Append a risk-mode posture block to the analyst system prompt.

    Unknown/None mode falls back to STANDARD. The base system prompt (the
    analyst's job + honesty rules) is preserved; the mode only sets posture."""
    key = mode if mode in MODES else DEFAULT_MODE
    _, _, _, rules = MODES[key]
    return f"{base_system}\n\n{rules}"


def label(mode: str) -> str:
    return MODES.get(mode, MODES[DEFAULT_MODE])[0]
