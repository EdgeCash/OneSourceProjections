# BYOE — Build Your Own Edge / Equation: feature report & OSP port plan

> Source-of-truth research for porting the "Build Your Own Equation" (BYOE)
> feature into **OneSourceProjections** (OSP). Written 2026-06-24.

## TL;DR

BYOE ("Build Your Own **Edge**", a.k.a. Build-Your-Own-Equation) lets a user
construct a **custom projection algorithm by weighting a handful of team
stats**, pick a **market** (moneyline / run line / total), a **model**
(transparent weighted z-score, or probabilistic Poisson) and a **staking**
rule (flat / Kelly / half-Kelly), then **backtest it live** against a season of
games and **save** it to compete on a leaderboard. A daily runner scores the
saved formula against every upcoming game and grades the picks.

**It is NOT AI-generated.** The "algorithm generation" is **weighted-factor
composition over league z-scores** — pure, deterministic math. No LLM call is
involved anywhere in the feature. (OSP's `onesource/ai.py` is unrelated and is
only relevant as an *optional* enhancement in the port plan.)

**The feature exists in two repos.** The **best / most complete** implementation
is **`edge-equation-v1`** — a clean pure-Python scoring engine + FastAPI router +
React builder + SQLite persistence + daily runner. A second, different design
lives in **`Sports-projections`** (`src/api/`), where a "formula" tunes the
*engine's own internal weight knobs* rather than weighting raw stats. The
edge-equation-v1 design is the one to port: it's self-contained, math is
exposed (portable), and it maps cleanly onto OSP's Python+Streamlit shape.

- **edge-equation-v1 source commit:** `599e6c8485bae087f306d5cd1b2793c0be7584f0`
- **Sports-projections source commit:** `9ccb19182187104fd6b25f8749ce29f03f16fd84`

---

## 1. Where it lives

### Primary implementation — `edge-equation-v1` (RECOMMENDED to port)

**Backend / engine (pure Python, `src/edge_equation/`):**

| Path | Role |
|---|---|
| `src/edge_equation/edges.py` | Data model + SQLite persistence. `Edge`, `EdgeInput`, `BoardPost` dataclasses; `EdgeStore`, `PickStore`, `BoardStore`, `FeedbackStore`. Validation (`MARKETS`, `MODELS`, `STAKING`, `MAX_INPUTS=12`). |
| `src/edge_equation/edge_scoring.py` | **The scoring engine** — the heart of BYOE. z-score index, weighted composite, weighted + Poisson models, Kelly staking, grading, `backtest()`. Pure functions, no I/O. |
| `src/edge_equation/persistence/db.py` | Schema (migrations) for `edges`, `edge_inputs`, `edge_picks`, `board_posts`, `feedback`. Lines ~182–273. |
| `api/routers/edges.py` | FastAPI router mounted at `/byoe`. Create/list/leaderboard/backtest/test_pick endpoints + board + feedback. |
| `tools/edges/run_edge_scoring.py` | Daily cron: scores upcoming games for every active Edge, logs immutable picks, grades finals. |

**Frontend (Next.js, `web/`):**

| Path | Role |
|---|---|
| `web/components/EdgeBuilder.tsx` | **The builder UI** — name, market, stat picker w/ weights, model/staking/visibility, live backtest panel, pick tester, save. |
| `web/lib/edge-stats.ts` | The curated **stat catalog** (`EDGE_STATS`) + `PRESETS` (house formulas) the picker iterates. |
| `web/lib/byoe-backtest-client.ts` | **Client-side** mirror of the scoring engine — runs the live backtest & single-pick test entirely in-browser against `games.json` / `team_extended.json`. |
| `web/components/EdgeBacktestPanel.tsx`, `EdgePickTester.tsx`, `BYOEFeedback.tsx` | Proving-ground panel, single-matchup tester, feedback widget. |
| `web/app/edges/new/page.tsx`, `edges/page.tsx`, `edges/board/page.tsx` | "Build a new Edge" page, leaderboard, discussion board. |
| `web/app/api/byoe/edges/route.ts` (+ `backtest/`, `test_pick/`, `board/`, `feedback/`) | Next.js route handlers that proxy the session cookie to the FastAPI `/byoe/*` endpoints. |

**Tests:** `tests/test_edge_scoring.py`, `tests/test_edge_runner.py`,
`tests/test_edges_store.py`, `tests/test_edges_schema.py`,
`tests_api/test_edges_router.py`.

### Secondary implementation — `Sports-projections` (`src/api/`)

A different concept: a formula = a saved set of values for the **engine's own
tunable weight knobs**, not raw-stat weights.

- `src/api/weights_schema.py` — catalog of tunable engine `WEIGHTS` per market, category grouping (Form & Momentum / Matchup Quality / Environment / Model Math), `HOUSE_FORMULAS` presets (`balanced`, `sharp`, `trend_chaser`, ...).
- `src/api/formulas.py` — `UserFormula` dataclass, `sanitize()` (clamp to declared min/max), `from_preset()`.
- `src/api/templates/formula_builder.html`, `formulas.html` — server-rendered builder UI.
- `src/api/app.py` — routes `/formulas`, `/formulas/new`, `/formulas/{id}`, `/api/weights`, `POST /api/formulas`, `POST /api/formulas/{id}/backtest`.
- `src/api/storage.py` — `list_formulas_for_user`, etc.

**IP-protection note (Sports-projections):** it deliberately exposes only
*abstract* labels + defaults of weights ("Park factor strength"); the engine
math behind each knob stays private. This is a meaningfully different (harder
to port, less self-contained) design than edge-equation-v1's exposed math.

> `profit-hunt` only contains BYOE *export/search tooling*
> (`edge-nrfi-engine-v1/tools/byoe_export.py`, `tools/research/byoe_search.py`),
> not a builder. `Sports-stats-data` has no BYOE feature (only the substring in
> odds storage). So the real candidates are the two above.

---

## 2. What it does (user-facing behavior — edge-equation-v1)

1. **Start from a preset (optional).** Three "house formulas" (Chalkboard
   Classic, The Run Forge, Slugger's Index) load a name + market + a sensible
   set of weighted stats to tune.
2. **Name it** and **pick a market**: Moneyline, Run Line (±1.5), or Total.
3. **Build the formula.** From a curated catalog of ~12 team stats grouped into
   *Form / Run Environment / Offense*, the user **adds stats** (max 12) and sets
   a **numeric weight** on each. Positive backs the stat, negative bets against
   it; weights are relative (normalized by total absolute weight).
4. **Pick a model:**
   - **Weighted z-score** — transparent ranker: the stronger composite is the
     pick. States a *side*, not a *price*. No probability.
   - **Poisson** — each composite bends a team's expected runs around the league
     average; runs treated as Poisson, so every pick carries a **win/cover
     probability** (the number Kelly needs).
5. **Pick staking:** Flat (1u every pick) / Kelly / Half-Kelly. Kelly needs the
   Poisson model's probability; with the weighted model it falls back to flat.
   Optional per-bet **max stake** cap (1u/3u/5u/10u/Unlimited).
6. **Live "Proving Ground" backtest.** On *every* formula change (debounced
   350ms) the builder re-scores the formula against every Final game and shows a
   record (W/L/push), units, hit-rate, ROI, and an equity curve. Runs
   client-side in-browser (`byoe-backtest-client.ts`).
7. **Single-pick tester.** Drop in a matchup (away/home codes) and see what the
   formula would pick, optionally compared to the user's own pick (`agrees`).
8. **Save & publish.** Posts to `/api/byoe/edges`. Saving archives the user's
   previous active Edge (record kept, stops picking) — **one active Edge per
   member**. Public Edges appear on a leaderboard ranked by units; private ones
   are graded but unlisted.
9. **Daily grading.** `run_edge_scoring.py` (cron) logs an immutable pick for
   each upcoming game and settles picks whose games went Final, accumulating the
   Edge's track record.

**Custom-equation representation (data model):** an `Edge` = `{name, market,
model, staking, visibility, max_stake}` + an ordered list of `EdgeInput =
{stat_key, weight}`. This is the saved "algorithm." Each `stat_key` must be a
real numeric field in the committed MLB data; the z-score index recognizes it.

**Output the user gets:** a saved, named algorithm with a live backtest record,
an immutable graded pick log, and a leaderboard rank.

---

## 3. How it works technically (edge-equation-v1)

**Generation logic = weighted-factor composition over z-scores.** No templates,
no AI. The pipeline (`edge_scoring.py`):

1. `build_team_index(team_form, team_batting)` → `{team_code: {stat_key: value}}`
   (numeric fields only; batting wins key collisions).
2. `zscore_index(team_index)` → restate every stat as a league z-score
   `(value − mean) / pstdev` per stat key (zero spread ⇒ z=0).
3. `_composite(z_index, code, edge)` → the formula applied to a team: weighted
   average of its z-scores, **normalized by total absolute weight** so scale is
   sane for 2 or 12 stats.
4. `score_game(edge, game, z_index)` → compares away vs home composites:
   - **weighted:** moneyline/run_line = stronger composite; total = sign of
     `away+home` composite (OVER if ≥0).
   - **poisson:** `_team_lambda(c) = LEAGUE_RPG · e^(γ·c)` (γ=0.13), then Skellam
     tails for moneyline (`_moneyline_probs`), shifted tails for run line
     (`_runline_cover_probs` at ±1.5), and a summed-Poisson PMF for the total
     (`_total_over_prob` vs the 9.0 league total). Returns pick + probability.
5. `stake_units(staking, prob, odds, max_units)` → flat=1u; Kelly =
   `_kelly_fraction(prob, odds) · 100`, half-Kelly halves it, capped by
   `max_units`. `_kelly_fraction` returns 0 at no edge (a Kelly bettor passes).
6. `grade_pick(market, pick, game, ...)` → settles vs the Final game
   (`ml_winner`/`rl_winner`/`total_result`), returns `{outcome, odds, stake,
   units}` using American-odds payout.
7. `backtest(edge, games, team_stats)` → loops Final games, returns
   `{summary:{graded,wins,losses,pushes,units,staked,hit_rate,roi}, ledger:[...
   per-pick rows with cumulative]}`. **Same code path as the live runner**, so a
   backtest row equals the bet the runner would have placed.

**Persistence (SQLite, `db.py`):**
- `edges(id, user_id, name, market, model, staking, visibility, max_stake,
  active, created_at, archived_at)` + partial unique index
  `idx_edges_one_active ON edges(user_id) WHERE active = 1` (enforces one active).
- `edge_inputs(id, edge_id, stat_key, weight)`.
- `edge_picks(id, edge_id, game_date, game_id, pick, prob, odds, outcome,
  stake, units, created_at, graded_at, UNIQUE(edge_id, game_id))` — immutable
  picks, no hindsight re-picks.

**API endpoints (FastAPI, prefix `/byoe`, `api/routers/edges.py`):**
- `POST /byoe/edges` — create (archives prior active). Subscriber-gated.
- `GET  /byoe/edges/mine` — caller's active Edge.
- `GET  /byoe/edges/leaderboard` — public Edges + settled records, ranked by units.
- `POST /byoe/edges/backtest` — score a *not-yet-saved* formula; nothing persisted.
- `POST /byoe/edges/test_pick` — single-matchup preview + `agrees` flag.
- `GET  /byoe/edges/{id}` — Edge + record + pick log (private = owner only).
- `POST /byoe/board`, `GET /byoe/board[/{id}]`, `POST /byoe/feedback` — community.

The Next.js `app/api/byoe/*` handlers are thin cookie-forwarding proxies;
status codes pass through (401→login, 403→upsell, 400→validation, 200→saved).

---

## 4. Dependencies

- **Python engine:** stdlib only — `math`, `statistics` (mean, pstdev),
  `dataclasses`, `datetime`, `sqlite3` via the repo's `Database` adapter. **No
  numpy, no pandas, no LLM.** This is what makes it trivially portable.
- **API layer:** `fastapi`, `pydantic` (only if you keep a REST layer; OSP is
  Streamlit so this is optional).
- **Frontend:** React/Next.js (not ported; OSP rebuilds the UI in Streamlit).
- **Data inputs:** two JSON files — `games.json` (per-game form + results +
  odds: `ml_winner`, `rl_winner`, `total_result`, `away_ml`, `home_ml`,
  `over_odds`, `rl_*_odds`, `status`) and `team_stats.json` / `team_extended.json`
  (per-team batting + form stats). No live network at scoring time.
- **No LLM calls anywhere in BYOE.** (Confirmed: grep for `anthropic|claude|
  openai|LLM` across `edges.py`, `edge_scoring.py`, the router — zero hits.)

---

## 5. Self-contained code excerpts (the core to port)

### 5a. z-score index + weighted composite (the "algorithm generator")
`src/edge_equation/edge_scoring.py`

```python
from statistics import mean, pstdev

def zscore_index(team_index):
    """Restate every stat as a league z-score: (value - mean) / stdev.
    A stat with zero spread contributes nothing (z = 0)."""
    stat_keys = {k for stats in team_index.values() for k in stats}
    moments = {}
    for key in stat_keys:
        values = [s[key] for s in team_index.values() if key in s]
        if not values:
            continue
        moments[key] = (mean(values), pstdev(values))
    out = {}
    for code, stats in team_index.items():
        z = {}
        for key, value in stats.items():
            mu, sigma = moments.get(key, (0.0, 0.0))
            z[key] = (value - mu) / sigma if sigma > 0 else 0.0
        out[code] = z
    return out

def _composite(z_index, code, edge):
    """One team's composite — the member's weights applied to its z-scores,
    normalised by total absolute weight (a weighted *average* of z-scores)."""
    z = z_index.get(code, {})
    raw = sum(inp.weight * z.get(inp.stat_key, 0.0) for inp in edge.inputs)
    scale = sum(abs(inp.weight) for inp in edge.inputs) or 1.0
    return raw / scale
```

### 5b. Scoring a game: weighted ranker + Poisson model
`src/edge_equation/edge_scoring.py`

```python
import math
LEAGUE_RPG = 4.5
POISSON_GAMMA = 0.13
_RUNS_CAP = 30

def _poisson_pmf_array(lam):
    out = []; p = math.exp(-lam)
    for k in range(_RUNS_CAP + 1):
        out.append(p); p = p * lam / (k + 1)
    return out

def _team_lambda(composite):
    c = max(-3.0, min(3.0, composite))
    return LEAGUE_RPG * math.exp(POISSON_GAMMA * c)

def _diff_tail(pa, pb, *, ge=None, le=None):
    total = 0.0
    for a, da in enumerate(pa):
        for b, db in enumerate(pb):
            d = a - b
            if ge is not None and d >= ge: total += da * db
            elif le is not None and d <= le: total += da * db
    return total

def _moneyline_probs(la, lb):
    pa, pb = _poisson_pmf_array(la), _poisson_pmf_array(lb)
    p_away = _diff_tail(pa, pb, ge=1); p_home = _diff_tail(pa, pb, le=-1)
    decided = p_away + p_home
    return (0.5, 0.5) if decided <= 0 else (p_away/decided, p_home/decided)

def score_game(edge, game, z_index):
    away, home = game.get("away_code"), game.get("home_code")
    if not away or not home: return None
    a, h = _composite(z_index, away, edge), _composite(z_index, home, edge)
    poisson = edge.model == "poisson"
    la, lb = _team_lambda(a), _team_lambda(h)
    prob = None
    if edge.market == "moneyline":
        pick = away if a > h else home
        if poisson:
            p_away, p_home = _moneyline_probs(la, lb)
            prob = p_away if pick == away else p_home
    elif edge.market == "run_line":
        if poisson:
            p_away, p_home = _runline_cover_probs(la, lb)
            pick, prob = (away, p_away) if p_away >= p_home else (home, p_home)
        else:
            pick = away if a > h else home
    elif edge.market == "total":
        if poisson:
            p_over = _total_over_prob(la, lb)
            pick, prob = (("OVER", p_over) if p_over >= 0.5
                          else ("UNDER", 1.0 - p_over))
        else:
            pick = "OVER" if (a + h) >= 0 else "UNDER"
    else:
        return None
    return {"game_id": game_id(game), "game_date": str(game.get("date") or ""),
            "pick": pick, "prob": round(prob, 4) if prob is not None else None}
```

### 5c. Kelly staking
`src/edge_equation/edge_scoring.py`

```python
KELLY_STAKE_SCALE = 100.0

def _decimal_odds(odds):
    if not isinstance(odds, (int, float)) or odds == 0: return 2.0
    return 1.0 + odds/100.0 if odds > 0 else 1.0 + 100.0/abs(odds)

def _kelly_fraction(prob, odds):
    b = _decimal_odds(odds) - 1.0
    if b <= 0: return 0.0
    return max(0.0, (b*prob - (1.0-prob)) / b)

def stake_units(staking, prob, odds, *, max_units=None):
    if staking == "flat" or prob is None: return 1.0
    units = _kelly_fraction(prob, odds) * KELLY_STAKE_SCALE
    if staking == "half_kelly": units *= 0.5
    if max_units is not None: units = min(units, max_units)
    return round(units, 4)
```

### 5d. Data model + persistence shape (to recreate in OSP)
`src/edge_equation/edges.py`

```python
@dataclass
class EdgeInput:
    stat_key: str
    weight: float

@dataclass
class Edge:
    id: int; user_id: int; name: str; market: str; model: str
    staking: str; visibility: str; active: bool; created_at: str
    archived_at: Optional[str]; max_stake: Optional[float] = None
    inputs: List[EdgeInput] = field(default_factory=list)

MARKETS  = ("moneyline", "total", "run_line")
MODELS   = ("weighted", "poisson")
STAKING  = ("flat", "kelly", "half_kelly")
MAX_INPUTS = 12
# create(): validate -> archive prior active -> INSERT edges + edge_inputs.
```

The curated stat catalog + presets (port the *shape*, re-key to OSP's stat
names) — `web/lib/edge-stats.ts`: `EDGE_STATS[{key,label,blurb,category}]` and
`PRESETS[{name,market,blurb,inputs:[{stat_key,weight}]}]`.

---

## 6. Port plan into OneSourceProjections

OSP is a **Python engine (`onesource/`) + Streamlit app (`app/`)**, data driven
by `data/output/latest.json`, with `onesource/ai.py` for optional Anthropic
analysis. BYOE ports almost verbatim because its engine is pure stdlib Python.

**Source commit to port from:** `edge-equation-v1` @
`599e6c8485bae087f306d5cd1b2793c0be7584f0`.

### Step 1 — New engine module: `onesource/byoe.py`
Port `edge_scoring.py` + the `Edge`/`EdgeInput` dataclasses from `edges.py` into
one module. Keep it pure (stdlib only — OSP already uses numpy/pandas elsewhere,
but BYOE doesn't need them). Public surface:
- `@dataclass Equation` (rename `Edge`): `name, market, model, staking,
  max_stake, inputs:list[EquationInput{stat_key, weight}]`.
- `zscore_index`, `composite`, `score_game`, `stake_units`, `grade_pick`,
  `backtest` — verbatim, minus the FastAPI-specific bits.
- **Adapter:** write `build_team_index(...)` to read OSP's own data instead of
  `games.json`/`team_stats.json`. OSP already has team rolling stats in
  `onesource/teamstats.py` (`league_ranks`, season/L10/L5 splits) and historical
  results in `onesource/history.py` — feed those into the z-score index. This is
  the only real integration work; everything downstream is unchanged.

### Step 2 — Stat catalog: `onesource/byoe_stats.py` (or a dict in `byoe.py`)
Port `EDGE_STATS` + `PRESETS`, **re-keyed to OSP's actual stat column names**
(from `teamstats.py` / the projections output). Group into categories for the
picker. Generalize beyond MLB if desired (OSP covers MLB + WNBA + NFL) — but to
ship fast, scope v1 to one sport whose team stats are readily available
(`MARKETS`/models are MLB-flavored; for WNBA/NFL you'd swap the Poisson run model
for the relevant scoring distribution, so **start MLB-only**).

### Step 3 — Persistence
OSP has a `data/` dir and SQLite usage in the engine. Two options:
- **Simple (recommended v1):** save equations as JSON under `data/` (one file
  per saved equation, or a single `data/byoe/equations.json`), mirroring
  `UserFormula.to_storage()`/`from_storage()` from Sports-projections
  (`src/api/formulas.py`) — clean dataclass↔dict round-trip, no DB migration.
  OSP is single-user, so the "one active per user" / archive logic from
  edge-equation-v1 simplifies to a list of named equations the user manages.
- **Full:** add an `edges`/`edge_inputs` table set mirroring `db.py` 182–227 if
  multi-user/leaderboard is wanted later. **Simplify away** the `user_id`,
  `visibility`, leaderboard, board, and feedback tables for a personal tool.

### Step 4 — Streamlit UI: `app/byoe.py` + a tab in `app/dashboard.py`
Recreate `EdgeBuilder.tsx` as Streamlit widgets:
- `st.text_input` name; `st.radio` market; `st.multiselect` + `st.number_input`
  per chosen stat for weights (cap 12); `st.radio` model + staking; optional max
  stake.
- **Live backtest:** on rerun call `onesource.byoe.backtest(equation, ...)` and
  render the summary + an equity curve via `st.line_chart(ledger.cumulative)`.
  (Streamlit reruns on every widget change, so you get the "Proving Ground"
  behavior for free — no debounce/JS port needed; drop
  `byoe-backtest-client.ts` entirely.)
- **Single-pick tester:** two `st.selectbox` team pickers → `score_game` on a
  synthetic game → show pick (+prob).
- **Save:** button → write JSON (Step 3). A "My Equations" list to load/delete.
- Register it in `app/dashboard.py` alongside the existing
  `from onesource import ai, config, dfs, edge, ... ` imports and the
  sport-nav/section layout.

### Step 5 — Daily scoring (optional)
Port `tools/edges/run_edge_scoring.py` to a script under OSP `scripts/` (OSP
already rewrites `latest.json` hourly via a GitHub Action) so saved equations
get scored/graded against the slate and accumulate a record. Reuse OSP's results
store (`onesource/results.py`, `onesource/scorecard.py`) for grading instead of
re-implementing `grade_pick`'s odds plumbing where OSP already has it.

### Step 6 — Optional AI layer (OSP-native enhancement, not in the source)
edge-equation-v1 has **no** AI in BYOE. OSP can add value: after a backtest,
build a markdown brief (equation + backtest summary + the model-vs-market edges
OSP already computes in `onesource/edge.py`) and pass it to
`onesource.ai.analyze_stream(brief, question)` (Claude Opus 4.8, adaptive
thinking — see `onesource/ai.py:61`) to critique the equation ("you're
over-weighting a noisy stat", "thin sample"). This reuses the existing
`available()` graceful-degradation pattern. Keep it strictly *additive* — the
core generator stays deterministic.

### What to simplify vs the source
- Drop the Next.js proxy layer, React components, and `byoe-backtest-client.ts`
  (Streamlit reruns replace the client engine).
- Drop multi-user concerns: `user_id`, subscriber gate, `visibility`,
  leaderboard, discussion board, feedback inbox (`BoardStore`, `FeedbackStore`,
  the `/byoe/board` + `/byoe/feedback` endpoints).
- Drop FastAPI/pydantic — Streamlit calls `onesource.byoe` functions directly.
- Keep, verbatim: `zscore_index`, composite, weighted + Poisson models, Kelly
  staking, `backtest`. This is the irreplaceable, ported core.

### Net new OSP files
- `onesource/byoe.py` — engine (ported `edge_scoring.py` + `Edge` model).
- `onesource/byoe_stats.py` — stat catalog + presets (re-keyed to OSP stats).
- `app/byoe.py` — Streamlit builder UI; wired into `app/dashboard.py`.
- `data/byoe/equations.json` (runtime) — saved custom equations.
- (optional) `scripts/run_byoe_scoring.py`, `tests/test_byoe.py`.
</content>
</invoke>
