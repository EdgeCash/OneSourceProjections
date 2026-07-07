# Projection-Accuracy Roadmap — synthesis of the 7-agent projection fleet

Goal (user's words): *"If we can accurately project Team A x.x, Team B y.y, total
z.z, then every game-based wager type falls into place."* This roadmap is about
the **projection** — the number — not the wagering/curation layer, which is
already strong (edge gate, calibration harness, CLV backtest).

Sources: `PROJECTIONS_{MLB,NBA_WNBA,NFL_NCAAF,NHL,SOCCER,TENNIS,CROSS_SPORT}.md`
(one research agent per sport-family + a cross-sport agent), each benchmarking
best-in-class public projection systems against our actual `project547` code.

---

## The one finding every sport agreed on

**We refine the win/margin side and let the total (and each side's score) fall
out as a raw, shrunk-average midpoint — and each sport already computes richer
signal in-repo that never reaches the game projection.**

- **MLB** — team-rate runs model discards the posted lineup, xwOBA, and the wind
  vector we already fetch (`models/game.py`). NRFI already does batter-vs-pitcher; the full-game model doesn't.
- **NBA/WNBA** — projects points from raw PPG; pace/ORtg/DRtg are computed in
  `teamstats.py` but only shown on the card, never used to project (`generic.py:83,147`).
- **NFL/NCAAF** — Elo/rest/EPA all target the margin; the **total is the raw
  schedule-unadjusted midpoint sum** (`generic.py:147`). Opponent-adjustment is
  coded but `opponent_adjust=False` for both. CFBD client is built but called nowhere.
- **NHL** — no goalie, no xG, no special teams anywhere in the game path; `_rest()`
  is computed then discarded (`pipeline.py:961` gate is false for NHL).
- **Soccer** — genuine Dixon-Coles *scoreline* but **nothing is ever fitted**: rho
  and team strengths are fixed constants, every match equal-weighted.
- **Tennis** — emits match-win probability only; structurally can't produce a
  games/sets total (Elo consumes winner/loser, discards the scoreline).
- **Cross-sport** — the published projection is never market-anchored, calibration
  is OFF for props and only just on for games, and the backtest **never scores the
  market as a baseline**, so "did this lever actually help?" is currently unanswerable.

The shared substrate is the multiplier: `generic.team_ratings` + `generic.expected_score`
feed NBA, WNBA, NFL, NCAAF, NHL, and (in goals) soccer. **One change there moves
six sports at once.**

---

## Build order (impact ÷ effort). Every lever ships behind a knob, dark, proven walk-forward first.

### TIER 0 — The measuring stick (do first; it gates everything else)
Nothing below can be judged without it. All [HAVE], low effort.
- **T0.1 Market-baseline skill score + CRPS in `backtest.py`.** Score the de-vigged
  close as the baseline every model must beat (its Brier is already loaded but never
  scored, `backtest.py:733`). Add CRPS for margin/total distributions. *This is the
  instrument. Prior EPA work proved why it matters: a lever can cut Brier yet do
  nothing for CLV.* → cross-sport #2.

### TIER 1 — Shared-substrate cheap wins (one change → many sports, all HAVE/DERIVE)
- **T1.1 Time-decay recency weighting** in `generic.team_ratings` (flat window →
  per-sport exponential half-life). → NBA #2, NFL #5, soccer #1, cross #4.
  **STATUS: BUILT + TESTED → NEGATIVE, parked at 0.0.** Capability shipped
  (`Sport.form_half_life`, `generic.decay_weights`), then swept half-lives
  {20,10,6,4} vs the T0.1 market baseline on NBA/NFL/NHL (2023–24). Moneyline
  Brier-skill barely moved and total-MAE-skill generally got *worse* at shorter
  half-lives; WNBA/MLS have no matched closing lines so can't be validated. Kept
  OFF everywhere pending contrary evidence — the measuring stick earning its keep
  (a plausible lever that does not beat the baseline, like the shelved EPA blend).
- **T1.2 Turn on opponent-adjustment (SoS)** for NFL & NCAAF (flag flip, already
  coded and used by NBA/NHL), and switch WNBA SoS to efficiency. → NFL #1, NBA #4.
- **T1.3 Reconcile total & side-scores with the Elo/rest blend.** After the blend,
  `home_exp − away_exp` no longer equals the published margin and the total absorbs
  no rest/Elo (`generic.py:196`). Correctness fix, cheap. → NBA #3, NFL #2.

### TIER 2 — Per-sport structural wins (bigger, high value, mostly no new feed)
- **T2.1 NBA/WNBA pace-and-efficiency engine** — points = `poss × (adj ORtg vs adj
  DRtg)/100`, total from projected possessions. [DERIVE], data already in `teamstats.py`. → NBA #1.
- **T2.2 MLB lineup-level batter×pitcher runs** — build each side's offensive base
  from the posted 9's mean wOBA via linear weights, blended into the team rate.
  [HAVE], biggest MLB lever. → MLB #1. **STATUS: BUILT + VALIDATED → ON at
  LINEUP_BLEND=0.35.** `scripts/validate_lineup_runs.py`, MLB 2024, 499 games with
  posted lineups: totals MAE and moneyline Brier both improve monotonically with
  the blend (MAE 3.450→3.372, Brier 0.2446→0.2415 at 0→1). First lever to clear
  the T0.1 bar. Conservative default (single validatable season — the 2023
  FanGraphs pull is blocked here — plus mild backtest lookahead the live as-of
  feed avoids). Follow-ups: strict as-of + Statcast xwOBA feed (MLB #2), platoon
  split vs the starter's hand, second validation season → then raise the blend.
- **T2.3 MLB wind vector** — already fetched (`weather.py:72`), read only for temp.
  Small, [HAVE]. → MLB #3.
- **T2.4 Soccer real Dixon-Coles MLE fit** — fit attack/def + home γ + rho jointly;
  yields per-league HFA and time-decay for free. [DERIVE]. → soccer #2.
- **T2.5 Tennis serve/return point model** → full games/sets distribution (the only
  path off match-win-only). [NEW DATA: Sackmann match CSVs]. → tennis #1.

### TIER 3 — Projection-time market anchoring + calibration polish (the CLV thesis)
- **T3.1 Market-seeded projection posterior** — anchor the *published* number to a
  **pre-close** (open/current) line, small per-market weight tuned on open→close CLV;
  **never** the close we grade against (that forces CLV→0). Efficient markets get
  ~zero weight; props/secondary sports get more. → cross #1.
- **T3.2 Calibration ON + extend to props + Platt** — harness built and proven safe,
  just switched off for props (`pipeline.py:670,730` pass no sport/market). → cross #3.
- **T3.3 Empirical-Bayes shrinkage** replacing the single fixed `RATING_SHRINK=0.65`. → cross #5.

### TIER 4 — New-feed dependent (timing edges; do after the free wins)
- **T4.1 NHL starting-goalie term (GSAx/dSv%)** — the biggest single-game swing in
  hockey, modeled at zero today. Binding constraint = a confirmed-starter feed. → NHL #1.
- **T4.2 NHL xG team ratings / special teams / empty-net.** → NHL #3,#4,#5.
- **T4.3 NBA injuries & projected-minutes feed** — dominant single-game error source;
  model is roster-blind. [NEW DATA]. → NBA #5.
- **T4.4 Soccer shot-xG inputs** (understat/fbref/Opta). → soccer #5.
- **T4.5 NFL live QB starter feed** — the ACCURACY_ROADMAP Stage 4 blocker; QB term
  helps the projection but was network-blocked. → NFL #4.

---

## KEY EMPIRICAL FINDING (from the T0.1 instrument)

Once the measuring stick existed, it answered the whole question bluntly. On every
game market with closing-line data (NBA/NFL/NHL, 2022–24), our model is **strictly
less accurate than the de-vigged closing line** — for both the winner and the total
— and accuracy improves **monotonically** as the published projection is blended
toward the market (λ = model→market share), validated against actual outcomes:

| Sport | total MAE λ=0 (model) → λ=1 (market) | ML Brier λ=0 → λ=1 |
|---|---|---|
| NBA | 15.07 → **13.53** | 0.208 → **0.187** |
| NFL | 10.46 → **10.01** | 0.219 → **0.207** |
| NHL | 1.87 → **1.79** | 0.238 → **0.225** |

No interior optimum: pure market is best at every step. Two Tier-1 reweighting
levers (T1.1 recency, T1.2 SoS) were tested and neither closed the gap — because
the gap is **structural** (coarse team-average inputs), not a weighting problem.

**Implications for the plan:**
1. The most accurate projection we can publish *today* is a heavily market-anchored
   one (T3.1). That's an immediate, validated accuracy win for the displayed
   "Team A x.x / Team B y.y / total z.z".
2. But a projection that *equals* the market has zero deviation → zero edge → no
   plays. Real plays require the model to beat the market *somewhere*, which today
   it does not on these markets. That only comes from the **structural** signal in
   Tier 2 (MLB lineup runs, NBA pace/efficiency, NHL goalie, soccer MLE/xG, tennis
   serve/return). Each must beat the T0.1 baseline before it earns weight *away*
   from the market anchor.
3. So the architecture is: **publish a market-anchored projection (accurate now),
   keep the model's independent deviation for edge detection, and grow that
   deviation's weight only where a structural lever proves it beats the close.**

## Sequencing rationale
1. **T0 first** — the market-baseline metric is the gate. Without it every later
   claim is an assertion, and the EPA history shows Brier-gains that were CLV-dead.
2. **T1 next** — three cheap changes to the shared substrate lift six sports; each
   is validated against the T0 instrument before going live.
3. **T2** — per-sport structural engines, in impact order; MLB and NBA are [HAVE]/[DERIVE].
4. **T3** — market anchoring is the highest-ceiling idea but must be built *on top
   of* the measuring stick, and separated from the existing downstream bet-selection
   shrink (today the two are conflated).
5. **T4** — the new-feed items are real but gated on data plumbing; sequence last.

Discipline is unchanged from the existing football work: **prove the edge in the
walk-forward backtest before wiring anything live.** Every lever lands behind a
`*_blend` / `*_coeff` knob at 0, validated, then turned up.
