# Ask-AI / Three-Mode Game Breakdown Feature — Port Report

**Source repo:** `Sports-projections` (a.k.a. "Edge Equation")
**Source commit:** `9ccb19182187104fd6b25f8749ce29f03f16fd84` (2026-06-24)
**Target:** `OneSourceProjections` (OSP), which already has `project547/ai.py` (Anthropic) and a Streamlit `app/`.

> Note on the LLM call below: when porting, default to the latest Claude models
> (`claude-opus-4-8` / `claude-sonnet-4-6`) per the consolidation. The **prompt
> wording is preserved verbatim** from the source — that wording is the crown jewel.

---

## 0. TL;DR — what this actually is

The feature was **NOT** found in `edge-equation-v1` (its `aggressive`/`conservative`
hits are all engine math: Kelly sizing, Monte-Carlo, calibration — unrelated).
The "Ask AI" three-mode feature lives entirely in **`Sports-projections`**, and it
comes in **two related forms**:

1. **Three styled slate-curation prompts** — Conservative / Standard / Aggressive
   "head curator" prompts rendered as copyable cards on the daily research page.
2. **"Build Your Own" single-game Ask-AI prompt builder** — an interactive
   widget on the same page where the user picks a *specific game*, a bet type, a
   side, and a **risk style (conservative / standard / aggressive)**, and the page
   assembles a copy-pasteable analysis prompt scoped to that one matchup with its
   engine view embedded.

Both are **client-side, copy-to-clipboard** prompts (the user pastes into
Claude.ai on their own subscription). The system prompts and the three modes are
defined server-side in Python; the per-game assembly happens in browser JS.

There is **also** a separate fully-automated server-side Claude pipeline
(`curation.py`) — a 4-persona + synthesis nightly batch — but that one is not the
three-mode Ask-AI; it is documented here only for the LLM-call reference (model
IDs, params, caching) because it is the repo's live Anthropic integration.

---

## 1. Where it lives — exact paths

### The three-mode prompt logic (crown jewels)
- **`Sports-projections/src/api/curation_styles.py`** — the whole thing.
  - Shared blocks: `_INTRO` (L33), `_HOWTO` (L41), `_gates()` (L61), `_output()` (L87), `_PERSONA_LINES` (L110).
  - **Per-mode rules:** `_RULES_STANDARD` (L121), `_RULES_CONSERVATIVE` (L143), `_RULES_AGGRESSIVE` (L169).
  - **Mode registry** `_STYLES` (L201) — maps each mode → (name, desc, accent hex, **anomaly %**, gate note, rules block).
  - `build_style_prompts(target_date)` (L258) — builds all three preambles + shared engine-candidate JSON.
  - **Single-game Ask-AI:** `CUSTOM_INTRO` (L293), `CUSTOM_OUTRO` (L312), `CUSTOM_STYLE_NOTES` (L323), `CUSTOM_BET_TYPES` (L334).
  - `full_prompt(style_key, target_date)` (L346) — convenience full pasteable prompt.

### The UI (buttons + interactive builder)
- **`Sports-projections/src/api/daily_html.py`**
  - `_render_prompts_section()` (L1725) — renders the three "Copy prompt" cards.
  - `_render_custom_builder()` (L1763) — renders the "Build Your Own" controls
    (game / bet type / side / **risk style** dropdowns + notes box).
  - JS (emitted inline, L1679–1718): `copyPrompt(key,btn)` (L1679),
    `cpBuild()` (L1685, assembles the single-game prompt), `cpCopy()` (L1716),
    `_copyText()` / `_fallbackCopy()` (clipboard helpers, L1664–1678).
  - Nav button "✨ AI Prompts" → `#sec-prompts` (L1835).

> `Sports-projections/public/matchups.html` and `public/prompts.html` are
> generated/static output artifacts, not source. The live source of truth is the
> two Python files above.

### Live Anthropic integration (for LLM-call reference only)
- **`Sports-projections/src/api/curation.py`** — `_real_curation()` (L454),
  `_call_persona()` (L386), `_synthesize()` (L406), model IDs + `PRICING` (L67–98).
- Persona/synthesis system prompts: `Sports-projections/src/api/curation_prompts.py`
  (imported at L388/L408).

### What OSP already has (the port target)
- **`OneSourceProjections/project547/ai.py`** — single-mode in-app analyst.
  `MODEL = claude-opus-4-8` (L20), one `SYSTEM` prompt (L22), `analyze_stream()`
  (L61) using `client.messages.stream(...)` with `thinking={"type":"adaptive"}`,
  `output_config={"effort":"medium"}`, `max_tokens=6000`.
- **`OneSourceProjections/app/ui.py`** — `ai_brief_game()` (L722),
  `ai_brief_prop()` (L767), `ai_brief_board()` (L832): build markdown briefs.
- **`OneSourceProjections/app/dashboard.py`** — `ai_block(brief, key)` (L259):
  the "🤖 Send to AI" expander (copy-paste primary + optional in-app analyze),
  wired into the game view at L379, prop view L608, board L729.

---

## 2. What it does — full behavior

### The three slate-curation modes
A "head curator" persona is asked to pick the most actionable plays from a slate,
using an attached research report + engine-candidate JSON. The **shared scaffolding
is identical** across modes (intro, how-to-read, data-quality gates, output YAML
format, the 4-persona sign-off checklist). Only these differ per mode:

| Lever | Conservative | Standard | Aggressive |
|---|---|---|---|
| Anomaly gate (`anomaly_pct`) | edge **> 20%** is auto-skip | **> 25%** | **> 30%** |
| Picks/day target | 2–4 (zero is common) | 3–7 | 5–10 |
| Default size | 0.5u | 1.0u | 1.0u |
| Max size | 1.5u (3 confirms + unanimous) | 2.0u (3 confirms) | 3.0u (3 confirms) |
| Correlation cap | 1 market/game, lowest-variance preferred | 1 market/game | up to 2 markets/game (≤2u combined) |
| Prop edge threshold | > 12%, **confirmed** lineup only | > 8%, lineup or announced | > 5%, lineup or announced |
| Noise floor (skip below) | edge < 5% | < 3% | < 2% |
| Persona sign-off | ALL FOUR (veto OR neutral kills it) | majority/veto rules | ≥2 of 4; only a hard Skeptic veto kills it |
| Accent color | `#3fb950` green | `#0ea5e9` blue | `#f59e0b` amber |
| Extra gate note | "when two reads disagree, pass" | (none) | "lean into the 10–25% edges others fade" |

So the modes change **bet-selection thresholds, sizing ladder, correlation
posture, and persona-veto strictness** — not the temperature (these are
copy-paste prompts; no API temperature is set). Standard is byte-for-byte the
prompt the daily brief always shipped; the other two are derived from it.

**User sees:** three cards (color-coded) each with a "Copy prompt" button. Clicking
copies that mode's full preamble **plus** the night's engine-candidate JSON, ready
to paste into Claude.ai. Output the model is asked to return: one fenced `yaml`
block of picks + 2–3 paragraphs (Why these plays / What we passed on / Persona
check log).

### The single-game "Build Your Own" Ask-AI
The user selects:
- **Game** (from the slate),
- **Bet type** (`CUSTOM_BET_TYPES`: any / moneyline / run_line / total /
  team_total / first_five / nrfi_yrfi / player_prop),
- **Side** (Auto / Over / Under / Home / Away),
- **Risk style** (conservative / standard / aggressive — defaults to standard),
- optional free-text **player/notes**.

`cpBuild()` (JS) assembles, live:
`CUSTOM_INTRO` + a "## Your request" block (game, bet type, side, risk style +
`CUSTOM_STYLE_NOTES[style]`, optional note) + a "## Engine view" block (for
player props, a pointer to the props table; otherwise the game's filtered
candidate markets as JSON) + `CUSTOM_OUTRO`.

**Game context fed to the AI:** team form (records/streak/L5), offense-vs-defense
split grids with league ranks, probable starters (MLB), player-prop hit rates
(L5/L10/season), the engine projection, and the market line — and for the
specific game, the filtered `markets` JSON (model projection vs market line per
market). The risk style here only swaps a one-line **style note** (sizing
posture) into the request; the analytical framing (`CUSTOM_INTRO`/`OUTRO`) is
shared. Output requested: Lean / Confidence 1–10 / Suggested size (within the
style's ladder) / EV read / Why (2–3 bullets) / Risks + a "research, not advice. 21+" line.

---

## 3. How it works technically

### Prompt assembly
- `build_style_prompts(target_date)` calls
  `scripts.build_daily_brief._build_engine_candidates(target_date)` once → a
  `{"mlb":[...], "wnba":[...]}` dict → `json.dumps(indent=2)`. Each game carries
  `away`, `home`, `start`, `markets[]` (each market = projection vs line).
- Each mode's `_preamble()` (L236) joins: banner + `_INTRO` + `_HOWTO` +
  `_gates(anomaly_pct, gate_note)` + `rules` + `_output(target)` + "## Engine candidates".
- Full prompt = `preamble + "\n\n```json\n" + candidates_json + "\n```\n"`.
- **Caching:** the candidate JSON is built once and shared by all three cards;
  appended client-side on copy (avoids re-embedding 3×). No LLM response caching
  here (copy-paste; no API call). The page itself is statically generated daily.

### API endpoints
The three-mode feature is **served as part of the daily HTML page** (rendered by
`daily_html.py`, mounted under the FastAPI app in `src/api/app.py`; e.g.
`/api/export/today.html`, L1444). There is **no dedicated JSON endpoint** for the
prompts — they are baked into the page and the LLM call happens in the user's own
chatbot, not server-side.

### The live LLM call (from `curation.py`, the automated pipeline — reference)
- Persona model `claude-haiku-4-5` (`PERSONA_MODEL`, L67); synthesis model
  `claude-sonnet-4-6` (`SYNTHESIS_MODEL`, L68, previously Opus 4.7).
- `client.messages.parse(...)` with `output_format=<pydantic model>` for
  structured output; `system=[{type,text,cache_control:{type:"ephemeral"}}]`
  (prompt caching) for personas (`max_tokens=400`), plain string system for
  synthesis (`max_tokens=8000`). No temperature set.
- Cost telemetry via `compute_cost()` + `PRICING` table; cache reads 0.1×,
  cache writes 1.25× base input.
- **For OSP, document the call as `claude-opus-4-8` (deep reads) /
  `claude-sonnet-4-6` (cheaper), matching `project547/ai.py`'s existing default.**

---

## 4. Dependencies

- **LLM provider:** Anthropic. SDK: `anthropic` (Python). OSP already uses it.
- Source `curation.py` models: `claude-haiku-4-5`, `claude-sonnet-4-6`
  (formerly `claude-opus-4-7`). Port default: `claude-opus-4-8` /
  `claude-sonnet-4-6`.
- Validation: `pydantic` (structured output schemas). Stdlib only for the
  three-mode prompt builder (`json`, `datetime`, `logging`).
- UI: vanilla JS clipboard (`navigator.clipboard` + `execCommand` fallback). No
  frontend framework — server-rendered HTML strings.

---

## 5. Self-contained code excerpts (verbatim)

### 5a. The three mode rule blocks (`curation_styles.py`)

```python
_RULES_STANDARD = f"""\
## Curation rules

1. **Be selective.** Aim for 3-7 picks per day. Zero is a
   valid output on a thin slate.
2. **Sizing (hard rules, not vibes):**
     - 1.0u default. Use this unless the play clears the bars below.
     - 1.5u requires TWO independent confirmations
       (e.g. engine edge AND recent form OR matchup angle).
     - 2.0u maximum, requires THREE confirmations. Reserve for
       your standouts of the day.
3. **Anti-correlation hard cap:** ONE market per game maximum.
   No stacking ML + over + team total on the same team. Pick
   the cleanest of the available edges and move on.
4. **Player-prop discipline:** only if (a) the player is in
   the lineup OR is the announced starter, AND (b) the engine
   edge is > 8%. Hold props until lineups confirm if uncertain.
5. **Skip noisy markets:** edge under 3%, no pick.
6. **Persona check** (one-line each per pick):
{_PERSONA_LINES}
   If any vetos: drop the pick."""

_RULES_CONSERVATIVE = f"""\
## Curation rules (CONSERVATIVE)

1. **Be highly selective.** Aim for 2-4 picks per day. Zero is a
   common and fully acceptable output -- protecting the bankroll
   beats forcing action.
2. **Sizing (hard rules, smaller ladder):**
     - 0.5u default. Use this unless the play clears the bars below.
     - 1.0u requires TWO independent confirmations
       (engine edge AND recent form OR matchup angle).
     - 1.5u maximum, requires THREE confirmations AND unanimous
       persona sign-off. Reserve for the single best play on the board.
3. **Anti-correlation hard cap:** ONE market per game maximum, no
   exceptions. Prefer the lowest-variance market available
   (moneyline / F5 over team-totals and props).
4. **Player-prop discipline:** only if (a) the player is CONFIRMED
   in the posted lineup (never "announced/likely"), AND (b) the
   engine edge is > 12%. When unsure, pass.
5. **Skip noisy markets:** edge under 5%, no pick. Avoid plus-money
   dart throws and high-variance overs.
6. **Lean on stable samples:** down-weight L5/L10 swings; require the
   season-long number to agree before sizing up.
7. **Persona check** (one-line each per pick):
{_PERSONA_LINES}
   ALL FOUR must sign off. Any veto OR any neutral: drop the pick."""

_RULES_AGGRESSIVE = f"""\
## Curation rules (AGGRESSIVE)

1. **Cast a wider net.** Aim for 5-10 picks per day. It's fine to
   be busy when the board is soft, but every pick still needs a
   real edge -- volume is not an excuse for noise.
2. **Sizing (hard rules, higher ceiling):**
     - 1.0u default.
     - 1.5u requires ONE confirmation beyond the engine edge.
     - 2.0u requires TWO confirmations.
     - 3.0u maximum, requires THREE confirmations. Reserve for your
       single highest-conviction play of the day.
3. **Correlation allowed, with eyes open:** up to TWO markets per
   game when they share a thesis (e.g. ML + over on a smash spot).
   Flag the correlation in the Money Manager line and never exceed
   2u of combined exposure on a single game.
4. **Player-prop aggression:** play props when (a) the player is in
   the lineup OR is the announced starter, AND (b) the engine edge
   is > 5%. Lean into model-vs-market gaps others ignore.
5. **Lower noise floor:** edge under 2%, no pick (still skip true
   coinflips).
6. **Hunt what the market hasn't caught:** prioritise the larger
   model-vs-market gaps and plus-money spots where the engine and
   recent form agree.
7. **Persona check** (one-line each per pick):
{_PERSONA_LINES}
   Majority rules: at least 2 of 4 sign off. Only a hard Skeptic
   veto (stale line / known bad data) kills a pick outright."""
```

Shared persona lines:
```python
_PERSONA_LINES = """\
     - The Quant: EV vs price?  __
     - The Capper: matchup / weather / park story?  __
     - The Money Manager: bankroll math + correlation OK?  __
     - The Skeptic: do we know something the market doesn't?  __"""
```

Mode registry (the per-mode anomaly %, accent, gate note):
```python
_STYLES = {
    "conservative": ("Conservative",
        "Capital-preservation mode: fewer, higher-conviction plays, smaller "
        "sizes, unanimous persona sign-off. When in doubt, pass.",
        "3fb950", 20,
        "- **When two reads disagree, pass.** A split between the model and "
        "recent form is a skip, not a coin-flip.",
        _RULES_CONSERVATIVE),
    "standard": ("Standard",
        "The house posture: selective, edge-anchored, one market per game, "
        "balanced sizing ladder.",
        "0ea5e9", 25, "", _RULES_STANDARD),
    "aggressive": ("Aggressive",
        "Action mode: more plays and a higher ceiling, taking variance to "
        "chase model edges the market hasn't caught.",
        "f59e0b", 30,
        "- **Lean into the 10-25% edges others fade** -- but still hard-skip "
        "anything that smells like a stale line or join error.",
        _RULES_AGGRESSIVE),
}
_STYLE_ORDER = ("conservative", "standard", "aggressive")
```

### 5b. Single-game Ask-AI prompt parts (`curation_styles.py`)

```python
CUSTOM_INTRO = """\
You are a betting analyst for Edge Equation. A subscriber wants your
independent read on a specific game and bet type. Use the matchup report
they're viewing (team form, offense-vs-defense splits with league ranks,
probable starters, and player-prop hit rates) together with the engine's
projection and the market line provided below. Edge Equation's model
produces edges, but this is research, not a directive -- give a clear,
honest read and let the subscriber decide.

## How to analyze
- Compare the model projection to the market line; quantify the gap and
  translate it to rough EV at the quoted price.
- Corroborate with the report: recent form (L5 / L10), matchup splits +
  league ranks, starter quality (MLB), park / weather / umpire, and
  bullpen for late-game totals.
- Apply the data-quality gates: be skeptical of > 25% edges (often a stale
  line or a join error), thin-prior pitchers (< 25 IP), and missing
  starter / umpire data."""

CUSTOM_OUTRO = """\
## Output
Give a tight verdict:
- **Lean:** the side you'd lean, or PASS, in one sentence.
- **Confidence:** 1-10.
- **Suggested size:** within the risk style's ladder above (or 0u / pass).
- **EV read:** model vs market, and the rough edge.
- **Why:** 2-3 bullets of the strongest supporting context.
- **Risks:** 1-2 bullets on what would make this wrong.
End with a one-line reminder that this is research, not betting advice. 21+."""

CUSTOM_STYLE_NOTES = {
    "conservative": "lean smaller, demand multiple confirmations, pass when "
                    "unsure (0.5u-1.5u).",
    "standard": "balanced; 1u default, up to 2u with multiple confirmations.",
    "aggressive": "more willing to take variance; 1u default, up to 3u on a "
                  "top-conviction read.",
}

CUSTOM_BET_TYPES = [
    ("any", "Any / let the model decide"),
    ("moneyline", "Moneyline"),
    ("run_line", "Run line / Spread"),
    ("total", "Total (Over/Under)"),
    ("team_total", "Team total"),
    ("first_five", "First 5 innings (F5)"),
    ("nrfi_yrfi", "NRFI / YRFI"),
    ("player_prop", "Player prop"),
]
```

### 5c. Core public builder (`curation_styles.py`, L258 + L346)

```python
def build_style_prompts(target_date: date) -> dict:
    try:
        from scripts.build_daily_brief import _build_engine_candidates
        candidates = _build_engine_candidates(target_date)
    except Exception as exc:
        log.warning("engine candidates build failed: %s", exc)
        candidates = {"mlb": [], "wnba": []}
    candidates_json = json.dumps(candidates, indent=2, default=str)
    styles = []
    for key in _STYLE_ORDER:
        name, desc, accent, *_ = _STYLES[key]
        styles.append({"key": key, "name": name, "desc": desc,
                       "accent": accent, "preamble": _preamble(key, target_date)})
    return {"candidates_json": candidates_json, "styles": styles}

def full_prompt(style_key: str, target_date: date) -> str:
    data = build_style_prompts(target_date)
    pre = next(s["preamble"] for s in data["styles"] if s["key"] == style_key)
    return f"{pre}\n\n```json\n{data['candidates_json']}\n```\n"
```

### 5d. The single-game assembler (browser JS, `daily_html.py` L1685)

```javascript
function cpBuild(){
  var el=document.getElementById('cp-data'); if(!el) return;
  var data; try{data=JSON.parse(el.textContent||'[]');}catch(e){return;}
  var gi=+document.getElementById('cp-game').value, g=data[gi];
  var out=document.getElementById('cp-out'); if(!g){out.textContent='';return;}
  var betSel=document.getElementById('cp-market');
  var bet=betSel.value, betLabel=betSel.options[betSel.selectedIndex].text;
  var side=document.getElementById('cp-side').value;
  var style=document.getElementById('cp-style').value;
  var note=(document.getElementById('cp-note').value||'').trim();
  var mk=g.markets||[];
  var ev;
  if(bet==='player_prop'){
    ev='## Engine view\nUse the Player Props table for this game in the report '
      +'(line, model projection, L5 / L10 / season hit rate, last-5 log). '
      +'Focus on the player + market named in the request and the chosen side.\n';
  }else{
    var rel=(bet==='any')?mk:mk.filter(function(m){return (m.market||'').indexOf(bet)===0;});
    if(!rel.length) rel=mk;
    var focus={away:g.away,home:g.home,sport:g.sport,start:g.start,markets:rel};
    ev='## Engine view for this game\n```json\n'+JSON.stringify(focus,null,2)+'\n```\n';
  }
  var styleNote=(window.CP_STYLES&&CP_STYLES[style])||'';
  var req='## Your request\n'
    +'Game: '+g.away+' @ '+g.home+' ('+g.sport+')\n'
    +'Bet type: '+betLabel+'\n'
    +'Side of interest: '+side+'\n'
    +'Risk style: '+style+' — '+styleNote+'\n'
    +(note?('Subscriber note: '+note+'\n'):'');
  out.textContent=(window.CP_INTRO||'')+'\n\n'+req+'\n'+ev+'\n'+(window.CP_OUTRO||'');
}
```

---

## 6. Port plan into OneSourceProjections

OSP already has the hard part: an Anthropic client (`project547/ai.py`), markdown
brief builders (`app/ui.py:ai_brief_game/prop/board`), and a "Send to AI" panel
(`app/dashboard.py:ai_block`). Today `ai_block` is **single-mode** (one `SYSTEM`
prompt, one button). The port adds the **three modes + per-game style picker**.

### Step 1 — Add a modes module: `project547/ai_modes.py`
Port `curation_styles.py`'s mode definitions verbatim (the wording is the asset).
Adapt the engine-context block to OSP's data instead of Edge Equation's
`_build_engine_candidates`. Expose:
- `MODES = {"conservative":..., "standard":..., "aggressive":...}` carrying, per
  mode: display name, description, accent, **anomaly %**, gate note, and the
  rules-block text (paste `_RULES_*` verbatim).
- `system_prompt(mode: str) -> str` — compose OSP's existing analyst `SYSTEM`
  (from `ai.py`) **plus** the mode's rules block + gate note, so the mode
  changes selection thresholds and sizing posture, matching the source.
- `style_note(mode)` from `CUSTOM_STYLE_NOTES` (verbatim) for the per-game read.

### Step 2 — Extend `project547/ai.py` to take a mode
Add a `mode` param to `analyze`/`analyze_stream`:
```python
def analyze_stream(brief, question=None, model=None, mode="standard"):
    system = ai_modes.system_prompt(mode)   # SYSTEM + mode rules/gate
    ...
```
Keep `claude-opus-4-8` default (and `claude-sonnet-4-6` as the cheaper option,
matching the source's split). Keep `thinking={"type":"adaptive"}` +
`output_config={"effort":"medium"}`. Optionally raise effort to `"high"` for
aggressive (more reasoning) — but no temperature knob is needed; the modes are
prompt-driven, exactly as in the source.

### Step 3 — OSP already supplies the game context
`ui.ai_brief_game(sport, g, matchup, min_edge)` (ui.py L722) already builds the
exact context the source feeds: model reads with per-market **conviction
scores** (`market_convictions`), **EV vs market** (`matchup_analysis`), **team
form** (`away_form`/`home_form`), and **biggest stat mismatches**
(offense-vs-defense league ranks). That brief *is* the "Engine view" block — no
new data plumbing required. `ai_brief_prop` covers the player-prop path. So OSP
can supply: projections, edges (EV), market lines, conviction, hit-rate splits,
and matchup stat mismatches — a superset of what the source embedded.

### Step 4 — Upgrade `ai_block` in `app/dashboard.py` (L259) to three modes
- Add a `st.radio`/`st.segmented_control` for **Conservative / Standard /
  Aggressive** (default Standard), keyed per game so it persists in
  `session_state`.
- Show three "Copy prompt" affordances OR one copy box that swaps with the
  selected mode: the copy text = `ai_modes.system_prompt(mode) + "\n\n" + brief`
  (mirrors the source's "preamble + engine JSON"). This is the **free
  copy-paste** path — primary, no API cost, exactly OSP's current ethos.
- The existing "✨ Analyze in-app" button calls
  `ai.analyze(brief, mode=selected_mode)` for the paid one-click read.
- Wire the mode into the three call sites already using `ai_block`: game view
  (dashboard L379), prop view (L608), board (L729).

### Step 5 — (Optional) per-game "Build Your Own" controls
For parity with the source's single-game builder, add bet-type + side dropdowns
(`CUSTOM_BET_TYPES` verbatim) above the copy box on the game-detail view, and
prepend `CUSTOM_INTRO` / append `CUSTOM_OUTRO` (verbatim) around the brief +
style note. In Streamlit this is server-side (`st.selectbox`), so no JS port is
needed — `cpBuild()`'s logic becomes a Python string-assembly function in `ui.py`.

### Step 6 — Files to add/edit (summary)
- **Add:** `project547/ai_modes.py` (mode registry + `system_prompt`, ported
  verbatim from `curation_styles.py`).
- **Edit:** `project547/ai.py` — add `mode` param threading the mode system prompt.
- **Edit:** `app/dashboard.py` `ai_block()` — add the 3-way mode selector + pass
  `mode` through.
- **Edit (optional):** `app/ui.py` — add a `build_custom_prompt()` helper
  (Python port of `cpBuild`) for the single-game builder.

### Source attribution
- Port from `Sports-projections` @ commit
  **`9ccb19182187104fd6b25f8749ce29f03f16fd84`**.
- Files: `src/api/curation_styles.py` (prompts), `src/api/daily_html.py`
  L1660–1830 (UI + JS), `src/api/curation.py` L67–98/386–448 (LLM-call params /
  caching reference).
