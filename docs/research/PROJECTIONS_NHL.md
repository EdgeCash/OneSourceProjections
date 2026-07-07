# Projecting NHL games — what best-in-class systems do vs. what we have

**Scope:** projection *accuracy* for Team A goals / Team B goals / game total.
Wagering logic (devig, shrink, Kelly, CLV) is downstream and out of scope here.
Audited file-by-file against the live engine, July 2026.

---

## The one-paragraph answer

Our NHL game projection is the **generic points model**: rate each team on **raw
goals for/against** over a 45-day window, shrink to the league mean, blend an Elo
win prob, combine the two ratings multiplicatively into an expected-goals lambda
per side, then draw two **independent Poissons**. It is a competent team-strength
engine but it is **missing every hockey-specific accuracy lever the best public
systems are built around**: (1) the **starting goalie as a first-class input** —
the single largest one-game swing in hockey and something we do not model *at
all*; (2) **shot-quality expected goals (xG)** instead of raw goals, which are
noisy and goalie-contaminated; (3) **special teams** (PP%/PK% × expected power
plays); (4) **rest / back-to-backs**, which for NHL is not merely absent but a
**hard no-op in code**; and (5) **empty-net / score-effect** handling on totals.
The goalie term is the headline: a confirmed backup vs. an elite starter can move
a side's expected goals by ~0.4–0.6 and a game total by ~0.4–0.7 goals — larger
than everything our model currently varies on combined.

---

## Part 1 — Exactly what our model does today

NHL is a `model="poisson"` sport (`project547/sports.py:168-177`) and runs the
**generic** path, not the MLB path. Trace:

**1. Slate + team ratings.** `project_generic_games("NHL", date)`
(`pipeline.py:906`) pulls the ESPN slate and the last `form_days=45`
(`sports.py:171`) of results, then builds ratings with
`generic.team_ratings(results, league_ppg=3.0, opponent_adjust=True)`
(`pipeline.py:917`, `generic.py:36-66`):

- Each team's `scored`/`allowed` is its **raw goals-for / goals-against per
  game**, shrunk toward the league mean 3.0 with weight
  `RATING_SHRINK(0.65) × min(1, n/10)` (`generic.py:26,54-56`). With 45 days of
  games (n ≥ 10) the weight saturates at 0.65 — so ~65% team, 35% league prior.
- `opponent_adjust=True` (`sports.py:176`) applies a **one-pass** strength-of-
  schedule correction (`generic.py:60-64`): facing weak defenses discounts your
  offense, etc. One pass only — not iterated to convergence.

**2. Elo.** Because `elo_blend=0.50` (`sports.py:174`), an Elo rating is
maintained over 500 days (`pipeline.py:922-933`) with `k=6, home_edge=50,
regress=0.30` — deliberately small k because a single hockey result carries
little signal.

**3. Expected goals per side.** `generic.project_game` →
`generic.expected_score` (`generic.py:69-88`) with `score_method="multiplicative"`
(`sports.py:172`), the log5-for-points form:

```
h_exp = 3.0 × (h_off/3.0) × (a_def/3.0) + hfa/2      # hfa = 0.15  -> +0.075
a_exp = 3.0 × (a_off/3.0) × (h_def/3.0) − hfa/2      #             -> −0.075
```

Home-ice is a flat **0.15 goals** split ±0.075 per side (`sports.py:170`). Each
side is floored at `league × 0.3 = 0.9` goals (`generic.py:88`).

**4. Distribution / win prob / total.** Two **independent Poissons** are drawn,
20,000 sims, `seed=7` (`generic.py:203-217`). Overtime is simulated by adding
`Poisson(lam/9)` increments to *both* sides until the tie breaks
(`generic.py:208-212`). Outputs (`generic.py:143-149`):

- `home_win_prob` = Poisson-sim win prob, then blended 0.50 with Elo
  (`pipeline.py:957-960`).
- `total_mean = h_exp + a_exp`.
- Totals priced as `1 − Poisson.cdf(int(line), lam_h+lam_a)`
  (`generic.py:104-109`); puck-line cover via `_poisson_cover`
  (`generic.py:220-222`). The total is thus a **pure Poisson (var = mean)**.

**5. Rest — a no-op.** `project_generic_games` computes `_rest(team)`
(`pipeline.py:944-949`) but the adjustment is gated behind
`if sport.rest_coeff and sport.sigma_margin > 0` (`pipeline.py:961`). NHL has
`rest_coeff=0.0` and `sigma_margin=0.0` (`sports.py:170`), and `shift_win_prob`
itself no-ops when `sigma <= 0` (`generic.py:124`). **Back-to-backs are never
applied to NHL.** `with_consistent_margin` also no-ops for Poisson sports
(`generic.py:188-194`).

**6. No goalie term.** Confirmed by grep: "goalie" appears only in the *skater/
saves prop* model (`models/nhl_props.py:34`), never in the game path. The saves
prop model even flags that it can't see shots-against (`nhl_props.py:13-15`).
The game lambda is a function of team ratings only — **who is in net is
invisible to the projection.**

### Feeds we actually have
- ESPN slate + historical results (goals only) — `clients/espn.py:60,393`.
- ESPN game **summary/boxscore** endpoint (`espn.py:144-166`) — carries NHL
  goalie lines (saves, shots-against) and often "probables," but is **not wired**
  into the game path.
- Committed skater/goalie box logs 2016–2025 (`scripts/import_nhl_skaters.py`),
  used only for props.
- BettingPros NHL markets incl. puck line / total / goalie props
  (`data/history/markets/nhl.json`).
- **No** xG feed, **no** confirmed-starting-goalie feed, **no** special-teams
  table wired.

---

## Part 2 — How the best NHL projection systems work

The public state of the art (MoneyPuck, Natural Stat Trick, Evolving-Hockey,
Dom Luszczyszyn's Net Rating at The Athletic, HockeyViz) shares a common shape.
None of them project off raw goals; all of them treat the goalie as a top input.

**Expected goals (xG) from shot quality, not raw goals.** Every serious model
rates teams on **xG for/against**, built from a shot-level model (distance,
angle, shot type, rush/rebound flags, strength state). Raw goals are a noisy,
low-count realization of xG and are heavily contaminated by *goalie* and
*shooting* variance; a team's xGF/60 stabilizes far faster (~2–3× fewer games)
than its GF/60. MoneyPuck's team ratings, Natural Stat Trick's core tables and
Evolving-Hockey's GAR all sit on top of a shot-quality xG. This is the hockey
analog of "EPA over points" that our football research already identified as the
#1 lever.

**Score- and venue-adjusted rates.** Because a leading team sits back (score
effects) and shot-recording rinks differ, the good systems use **score-and-venue
adjusted** xG so a rating isn't polluted by game state. Our raw goals include all
of this noise.

**The goalie as a first-class input.** This is where hockey diverges most from
other sports. A team's goals-against on a given night is dominated by *which
goalie starts* and how far above/below expected he stops pucks (**GSAx** =
goals saved above expected; equivalently **dSv%**, save% over expected). Elite
starters (Hellebuyck, Bobrovsky, Sorokin tier) run roughly +0.3 to +0.6 goals
saved above an average goalie per start; a replacement backup can be −0.3 or
worse. The pipeline is: **confirmed starter → that goalie's GSAx/60 (regressed to
his true talent and to the mean) → adjust the opponent's expected goals.** Dom
Luszczyszyn's model and MoneyPuck both flip their projection materially on
starter confirmation, and the market visibly moves totals **0.3–0.7 goals** and
moneylines 15–40 cents when a starter is confirmed or scratched. A model with no
goalie term is systematically wrong on exactly the games where the edge is
largest.

**Special teams.** Goals split into even-strength, power-play, and short-handed.
Good models carry **PP% and PK%** and an estimate of **expected power plays**
(team penalty-drawn/taken rates, and referee tendency). A strong PP vs. a weak PK,
plus a high-penalty ref, adds meaningfully to a side's expected goals — invisible
to a single even-strength-blind team rate.

**Rest, back-to-backs, travel.** The second game of a back-to-back — especially
for the *goalie* (backups usually start the back half) and on the road — is a
well-established, market-priced negative (~0.15–0.30 goals / a few points of win
prob). Three-in-four and heavy travel compound it.

**Home ice.** NHL home-ice is real but small — about **54–55%** raw home win
rate, ~0.15–0.20 goals. Our 0.15 total is in range.

**Empty-net effects on totals.** Trailing teams pull the goalie for a 6th
skater late; **empty-net goals add ~0.2–0.3 goals per game to totals** and are
concentrated in one-goal games. Totals models that ignore EN are biased low, and
the *shape* matters (EN goals are conditional on a close game late).

**Distribution: Poisson, with a whisper of over-dispersion.** Single-team
regulation goals are close to Poisson at mean ~3, which validates our Poisson
core. But real NHL final scores are **slightly over-dispersed** (var/mean modestly
above 1) once you fold in OT/shootout scoring, empty-netters, and score effects,
and the two sides are **mildly negatively correlated** within a game (score
effects). The best totals models use a small over-dispersion (negative binomial)
and handle OT/SO scoring explicitly rather than a pure sum-of-Poissons.

**The sharp market.** Pinnacle/Circa closing totals and pucklines are the
benchmark; the systems above are judged on beating (or matching) the close, and
they treat *confirmed goalie* as the single biggest pre-close information event.

---

## Part 3 — Gap analysis, ranked by impact ÷ effort

| # | Technique | Accuracy impact | Data-readiness | Rank (impact÷effort) |
|---|---|---|---|---|
| **1** | **Starting-goalie term (GSAx/dSv%)** | **HIGH** — biggest single-game swing in hockey; ~0.4–0.7 goals on totals, 15–40c on the line | **[NEW DATA]** confirmed-starter feed + **[DERIVE]** goalie GSAx from box logs | **★ Top** |
| 2 | Rest / back-to-back adjustment | MED–HIGH — measurable, market-priced; also enables goalie-B2B logic | **[HAVE]** — `_rest()` already computed, just gated off | **★ Top (near-zero effort)** |
| 3 | xG-based team ratings (score/venue adj.) | HIGH — stabilizes ratings, de-noises raw goals | **[NEW DATA]** shot-level xG feed (MoneyPuck/NST) or derive from ESPN shot coords | High impact, high effort |
| 4 | Special teams (PP%/PK% × expected PP) | MED | **[DERIVE]** from box logs / **[NEW DATA]** team ST table | Medium |
| 5 | Empty-net / score-effect on totals | MED (totals bias) | **[DERIVE]** — constant or conditional bump | Medium |
| 6 | Over-dispersed total distribution (NB) | LOW–MED | **[HAVE]** — `_nb_prob_over` already exists for MLB | Low effort, modest impact |

### 1. Starting-goalie term — **the headline gap** ★

- **(a) Technique.** Adjust each side's expected-goals lambda by the *opponent's
  confirmed starting goalie's* goals-saved-above-expected. Concretely:
  `a_def_effective = a_def × f(home_goalie_GSAx)` where an elite starter shrinks
  the goals you'll allow and a backup inflates them, regressed to goalie true
  talent and to the mean by sample size.
- **(b) Impact: HIGH.** This is the largest one-game lever in the sport and the
  one our model is completely blind to. Because we *do* have goalie box logs, the
  talent estimate is cheap; the binding constraint is the **confirmed-starter**
  signal.
- **(c) Our gap.** Zero goalie awareness in the game path (`generic.expected_score`
  uses team `allowed` only, `generic.py:83-87`). We even note in the *props* model
  that we don't model shots-against/save% (`nhl_props.py:13-15`).
- **(d) Data-readiness.** `[NEW DATA]` **confirmed-goalie feed** — options:
  ESPN summary "probables" (`espn.py:_summary`, already reachable), FantasyPros
  NHL news/injuries (already pulled — `pipeline.py:1702-1744`), or a dedicated
  starting-goalie source. `[DERIVE]` **GSAx/dSv%** per goalie from the committed
  goalie logs (`scripts/import_nhl_skaters.py` already imports saves; add
  shots-against → save% over a league/opponent-shot-volume baseline).
- **(e) Implementation sketch.**
  - New `models/nhl_goalie.py`: `goalie_rating(goalie_id) -> dSv%` (regressed),
    fit from the goalie logs the same walk-forward way `nhl_props` fits its
    dispersions (`scripts/validate_nhl_props.py`).
  - Add optional `home_goalie` / `away_goalie` params to
    `generic.expected_score` (or a thin NHL wrapper around it) that scale the
    opponent's `*_def` term by `(1 − dSv%_adj)`, clamped.
  - In `project_generic_games` (`pipeline.py:951-968`), resolve each side's
    confirmed starter before calling `project_game` and thread the ratings in.
  - **Gate it** behind validation exactly like `epa_blend`/`qb_coeff`
    (`sports.py:56-61`): ship dark, prove it beats the close on the backtest,
    then turn on. Flag prominently — this is #1.

### 2. Rest / back-to-back — **fix the no-op** ★ (near-zero effort)

- **(a)** Apply a rest-days edge to the NHL win prob (and, ideally, a small goals
  bump), as the other sports already do.
- **(b) Impact: MED–HIGH** for near-zero effort — the machinery already exists.
- **(c) Our gap.** `_rest()` is computed (`pipeline.py:944-949`) but discarded:
  the gate `sport.rest_coeff and sport.sigma_margin > 0` (`pipeline.py:961`) is
  false for NHL (`rest_coeff=0.0, sigma_margin=0.0`), and `shift_win_prob`
  no-ops on Poisson anyway (`generic.py:124`).
- **(d) Data-readiness.** `[HAVE]` — rest is derived from results we already pull.
- **(e) Implementation sketch.** Poisson sports need a different hook than the
  normal-model `shift_win_prob`. Two clean options: (i) set an NHL `rest_coeff`
  and add a Poisson-aware branch that nudges the two lambdas (e.g. tired road side
  ×(1−δ)); or (ii) convert a small rest delta to a win-prob nudge via a fixed NHL
  margin sigma. Tie it to the goalie term (backups start most back-to-back
  second legs) for compounding accuracy. Tune `rest_coeff` on the game backtest
  before enabling, per house discipline.

### 3. xG-based team ratings

- **(a)** Replace/blend raw goals-for-against with **score-and-venue-adjusted
  xG** for the `scored`/`allowed` inputs to `team_ratings`.
- **(b) Impact: HIGH** but **effort HIGH** (new shot-level feed + pipeline).
- **(c) Our gap.** `team_ratings` consumes `home_score/away_score` raw goals only
  (`generic.py:45-49`).
- **(d) Data-readiness.** `[NEW DATA]` — MoneyPuck/Natural Stat Trick team xG
  tables, or `[DERIVE]` an xG from ESPN shot coordinates (heavier build).
- **(e) Implementation sketch.** Add an NHL-specific ratings builder that feeds
  xGF/xGA into the same `TeamRating` shape, so `expected_score` is unchanged.
  Mirror the football **EPA** staging (`epa.py`, `epa_blend` in `sports.py:56-61`):
  land the data + a `xg_blend` knob at 0, validate walk-forward, then blend in.

### 4. Special teams (PP%/PK% × expected power plays)

- **(a)** Decompose expected goals into even-strength + power-play contributions:
  `PP_goals ≈ expected_PP_opportunities × team_PP% (vs opp PK%)`.
- **(b) Impact: MED.** **(c)** Not modeled — single team rate only.
- **(d)** `[DERIVE]` PP%/PK% from box logs / `[NEW DATA]` a team ST table.
- **(e)** Add a special-teams add-on inside the NHL expected-goals wrapper before
  the Poisson draw; keep even-strength as the log5 core.

### 5. Empty-net / score-effect on totals

- **(a)** Add the empty-net component to totals (a small constant, or better,
  conditional on a close-and-late game state).
- **(b) Impact: MED** (systematic low bias on totals). **(c)** Absent.
- **(d)** `[DERIVE]` — league EN rate from logs; no new feed.
- **(e)** Bump `total_mean` / the sampled totals by ~+0.2 in
  `generic.project_game` for NHL, or model it in the OT/late block of
  `_poisson_draws` (`generic.py:203-212`), which currently also mishandles
  OT/shootout scoring by adding `Poisson(lam/9)` to *both* sides.

### 6. Over-dispersed total distribution

- **(a)** Price the total off a **negative binomial** (var = mean × d) instead of
  a pure Poisson, to capture mild over-dispersion + OT/SO + EN.
- **(b) Impact: LOW–MED.** **(c)** `prob_over` uses pure Poisson
  (`generic.py:104-109`).
- **(d)** `[HAVE]` — `_nb_prob_over` already exists (`pipeline.py:96-109`),
  currently MLB-only.
- **(e)** Give NHL a small `RUN_DISPERSION`-equivalent and route its total
  through the existing NB helper; tune d on the backtest.

---

## Bottom line

Our NHL engine is a solid *team-strength* model with the betting scaffolding
already right, but it is missing the hockey-specific signal that separates good
from great — and one of those gaps (rest) is a wire that's already built and
simply switched off. Priority order: **(1) goalie term**, **(2) turn on rest**,
**(3) xG ratings**, then special teams / empty-net / over-dispersion. Every
code-level change should land dark behind a `*_blend`/`*_coeff` knob and be
proven walk-forward before it goes live, exactly like `epa_blend` and `qb_coeff`.
