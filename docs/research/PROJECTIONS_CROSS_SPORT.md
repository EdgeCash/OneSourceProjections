# Cross-Sport Projection Best-Practices — Gap Analysis

**Scope:** sport-agnostic techniques that lift Team-A / Team-B / total *projection
accuracy* across every sport at once. Wagering/curation logic is downstream and
out of scope here. North-star metric: beating the close (CLV), with well-calibrated
probabilities (Brier / log-loss) as the leading indicator.

**Method:** file-by-file audit of the live engine (July 2026) benchmarked against
the elite-system playbook already captured in `docs/research/03-modeling-calibration-evaluation.md`
and `00-synthesis.md`. Every "we currently…" claim below cites a real `file:line`.

This document is deliberately the *cross-sport* companion to the existing
NFL/NCAAF-specific `ACCURACY_ROADMAP.md`: that roadmap's biggest lever (EPA inputs)
is football-only; the levers here lift **all** sports (MLB, NBA, WNBA, NFL, NCAAF,
NHL, soccer, tennis) because they touch the shared machinery in
`models/generic.py`, `models/elo.py`, `calibrate.py`, `pipeline.py`, and `backtest.py`.

---

## STEP 1 — How the engine works today (the shared spine)

### Recency weighting — flat window, no time-decay
`generic.team_ratings` averages every game in a fixed lookback window with **equal
weight**, then shrinks that flat mean toward the league average:

- `models/generic.py:52-56` — `w = RATING_SHRINK * min(1.0, n/10)`, then
  `w * mean(scored) + (1-w) * league_ppg`. Every game in the window counts the
  same; a game 40 days ago weighs exactly as much as last night.
- Window length is per-sport `form_days` (`sports.py`, e.g. WNBA 45, NFL 140) and,
  in the walk-forward `_Form`, a hard game count (`backtest.py:554`, window 15/30).
- The backtest replicates the identical flat-window math (`backtest.py:310-317`).

There is **no exponential decay / half-life** anywhere in team rating construction.
The only time-dynamics are Elo's per-update step (`elo.py:67`) and its between-season
regression (`elo.py:36-42`).

### Priors — a single fixed shrinkage constant, one global mean
- `models/generic.py:26` — `RATING_SHRINK = 0.65`, duplicated as
  `config.py:82 TEAM_RATE_WEIGHT = 0.65`.
- The prior every team is pulled toward is the **single league mean** (`league_ppg`,
  `sports.py`), identical for the best and worst team. Shrinkage strength ramps only
  with sample size (`min(1, n/10)`), not with the team's *variance* or a
  team-specific prior.
- Cold-start teams get pure `league_ppg` (`generic.expected_score:74-78`). No
  preseason / carryover prior except Elo's fixed `season_regress`
  (`sports.py`: 0.25–0.5 per league).

### Shrink to the mean vs. shrink to the market — two different things, only one is at projection time
This is the crux of the whole report, so it is worth stating precisely:

- **Shrink to the league mean** happens at *projection* time (`generic.py:52-56`)
  and shapes the published number.
- **Shrink to the market** happens only at *EV* time, inside `_market_eval`
  (`pipeline.py:40-93`): `p = odds.blend_toward_market(p_model_a, p_fair, shrink)`
  (`pipeline.py:75`, using `odds.py:103-108`), weight `MARKET_SHRINK=0.5`
  (`config.py:225`) / per-sport `market_shrink` (`sports.py:67`, MLB 0.65).
- **The published projection is never anchored to the market.**
  `project_generic_games` builds `home_win_prob` from the off/def model + Elo blend
  + rest nudge and writes it raw (`pipeline.py:956-969`, `979`); `attach_game_edges`
  only *adds EV columns* — it never rewrites `home_win_prob`
  (`pipeline.py:801-899`). So the number the site shows, and the number the
  backtest grades, is pure model.

The config comment is explicit that market-shrink is treated as a *bet-selection*
knob, not a *calibration* one (`config.py:222-224`): "at 1.0 nothing clears
MIN_EDGE." Correct for bet selection — but it means the **accuracy** benefit of
market anchoring is currently left on the table.

### Calibration — built, validated-safe, but inert and partial
- `calibrate.py` applies a per-`(sport, market)` isotonic map via `np.interp`;
  `scripts/fit_calibration.py` fits it (Pool-Adjacent-Violators isotonic, no Platt),
  with a proper **time-disjoint 80/20 holdout** and a "only WRITE if it doesn't
  worsen holdout Brier" gate (`fit_calibration.py:159-195`), `MIN_FIT=400`.
- It is **OFF by default**: `config.APPLY_CALIBRATION` (`config.py:235`) reads an
  env flag defaulting false; `pipeline.py:62-64` only calibrates when the flag is
  on **and** `sport`+`market` are passed.
- It is **partial even when on**: props call `_market_eval(p, price, None)` with no
  `sport`/`market` (`pipeline.py:670, 730`), so **props are never calibrated**.
  Only the two-way game markets (`pipeline.py:813, 845, 876`) pass the keys.
- **Order coupling:** when on, calibration is applied to the model prob and then
  *shrunk toward market* (`pipeline.py:62-75`) — two overlapping corrections whose
  interaction is never measured.

### Distribution / variance — a genuine strength
- Per-sport single-game σ measured from walk-forward residuals
  (`sports.py`, e.g. WNBA `sigma_margin=12.7, sigma_total=17.0`; NFL 16.0/13.5),
  used in `generic.prob_over` / `home_cover_prob` (`generic.py:104-117`).
- Over-dispersion is handled: MLB runs a negative-binomial run sim
  (`config.py:154 RUN_DISPERSION=2.3`, empirically = measured var/mean) and props
  use per-market NB dispersion (`generic.py:236-273`).
- Margin/moneyline/spread are kept mutually coherent by back-solving the margin
  from the adjusted win prob (`generic.with_consistent_margin:173-200`,
  wired at `pipeline.py:968` and `backtest.py:627-629`).

### Evaluation — solid walk-forward, but no market baseline
- `run_game_backtest` is genuinely walk-forward with no lookahead: ratings built
  only from prior games (`backtest.py:585-588`), CLV graded at **closing** prices
  (`backtest.py:646-684`).
- It reports Brier, log-loss, favorite-hit-rate, total MAE/RMSE, a 10-bin
  calibration table, `avg_clv_vs_fair`, and bet ROI (`backtest.py:733-757`).
- **What's missing:** it never computes the **market's own** Brier/log-loss as the
  bar to beat, so "is the model adding anything over free information?" is
  unanswered by the metrics it prints. CLV is computed on the **raw** model prob
  (`backtest.py:651 clv_deltas.append(hwp - m["home_fair"])`), not the
  shrunk/calibrated prob that is actually bet (`backtest.py:655`) — the reported
  CLV doesn't correspond to the deployed pipeline. No CRPS for the distributions.

---

## STEP 2 — The elite-system benchmark (cross-sport)

Condensed from `03-modeling-calibration-evaluation.md` (full citations/URLs there):

1. **The market is the strongest single predictor.** The de-vigged closing line
   out-forecasts almost any feature pipeline. Elite systems **regress the model
   toward the market** (small model weight, `w≈0.1–0.3`) and *measure skill as
   calibrated deviation-from-market*, validated on **CLV**.
2. **Time-decay recency**, not flat windows: exponential half-lives tuned per sport
   (short for high-variance/short seasons, longer for stable leagues).
3. **Empirical-Bayes / hierarchical priors** instead of one fixed shrinkage
   constant: pull strength scales with each estimate's uncertainty; tier-aware so
   elite/bottom teams aren't over-shrunk.
4. **Calibration is the highest-leverage single step** (isotonic *and* Platt; Platt
   for low-sample markets), per-market, on a time-disjoint holdout, re-checked for
   drift, with reliability diagrams + ECE < 0.05.
5. **Ensemble** power-rating + score-based + **market** with **learned** stacker
   weights, then calibrate the stacked output.
6. **Predict a full distribution** (mean *and* correct σ, over-dispersion, key
   numbers), because the bet is `P(stat > line)`, not the mean.
7. **Coherent joint margin+total**: sides/totals/spreads must reconcile; margin and
   total are correlated and a key-number-aware margin distribution beats a plain
   Gaussian near ±3/±7.
8. **HFA estimated per-league from data and shrunk**, not hardcoded; flag neutral
   sites.
9. **Regression-to-mean with carried-over preseason priors** (returning production,
   prior-season rating) instead of cold-start league average.
10. **Rigorous walk-forward evaluation** on proper scoring rules **relative to the
    market baseline** (skill score), plus CRPS, plus CLV over 300–500+ events.

---

## STEP 3 — Gap analysis (ranked by impact ÷ effort, all sports)

Each row: (a) technique · (b) accuracy impact + why · (c) our gap with `file:line`
· (d) data-readiness · (e) implementation sketch + which sports it lifts.

Data-readiness legend: **[HAVE]** already on disk / computed · **[DERIVE]**
computable from existing data · **[NEW DATA]** needs a feed we don't ingest.

---

### #1 — Market-seeded / market-anchored projections  · impact HIGH · effort MED
**(The single most important lever — see the dedicated section below.)**

- **Technique:** make the *published projection itself* a posterior that blends the
  model's number with the de-vigged reference (opening / current) line, weight `w`
  on the model small and tuned per sport/market by walk-forward CLV. Report skill as
  calibrated deviation-from-market.
- **Impact:** HIGH. The close is the strongest predictor; anchoring collapses the
  model's noisy false edges (the exact failure mode config.py:210-213 already
  describes — "far too many fat edges … over-confidence + stale inputs, not alpha")
  and is the technique research says "reliably beats the closing line." Lifts every
  sport that has an odds feed (MLB, NBA, WNBA, NFL, NHL, soccer, tennis).
- **Gap:** the blend exists but only as a **bet-selection** step in `_market_eval`
  (`pipeline.py:75`) — the projection (`pipeline.py:956-969`) and the CLV metric
  (`backtest.py:651`) both run off the **raw** model. There is no market term in the
  published number.
- **Readiness:** **[HAVE]** — `odds.blend_toward_market` (`odds.py:103`), de-vig
  (`odds.fair_two_way`), snapshot closes (`clv.py`), and the open→close CLV harness
  (`backtest.run_mlb_clv_open_close:820`) all exist.
- **Sketch:** introduce a projection-time `market_seed(p_model, p_reference, w)`
  applied in `project_generic_games`/`project_games` *before* the row is emitted, so
  the displayed number and the graded CLV are the posterior. Tune `w` per
  (sport, market) on `run_mlb_clv_open_close`-style open→close CLV, not on Brier
  alone. **Keep the reference line = open/current, grade CLV vs close** (never anchor
  to the same close you grade against — that mechanically forces CLV→0).

---

### #2 — Evaluate against the market baseline (skill score) + CRPS  · impact HIGH · effort LOW
- **Technique:** report the **de-vigged close's own Brier/log-loss** next to the
  model's, as a skill score (`1 − Brier_model/Brier_market`); add CRPS for the
  margin/total distributions. This is the instrument that tells you whether #1 (or
  anything) actually helped.
- **Impact:** HIGH (as an enabler). Without a market baseline, every other change is
  flying blind — "Brier 0.216" is meaningless unless you know the close scores
  0.20x on the same games. Cheap and lifts confidence in every subsequent lever.
- **Gap:** `run_game_backtest` computes model Brier/log-loss and `avg_clv_vs_fair`
  but **never scores the market probs it already loads** (`m["home_fair"]`,
  `t["over_fair"]`) as a baseline; no CRPS (`backtest.py:733-757`). CLV is measured
  on raw `hwp`, not the bet prob (`backtest.py:651` vs `655`).
- **Readiness:** **[HAVE]** — `home_fair`/`over_fair`/`home_fair` (spread) are
  already in `consensus` (`backtest.py:469-483`).
- **Sketch:** in the accuracy loop, accumulate `brier_market += (m["home_fair"] −
  home_won)**2` (and log-loss) wherever a close exists; emit
  `moneyline.brier_market` + `skill_score`. Add a `_crps_normal(mu, sigma, actual)`
  for margin/total. Grade CLV on the deployed (shrunk) prob to match production.

---

### #3 — Turn calibration ON, extend to props, add Platt for thin markets  · impact MED-HIGH · effort LOW
- **Technique:** post-hoc calibration is the highest-leverage single step in the
  literature. Use isotonic where data is plentiful, **Platt** (2-param logistic)
  where it's scarce (props, secondary leagues), per market, drift-checked.
- **Impact:** MED-HIGH. The harness is already built and *proven-safe*
  (write-only-if-not-worse gate). It is simply switched off and doesn't cover props.
- **Gap:** `APPLY_CALIBRATION` defaults false (`config.py:235`); props bypass it
  (no `sport`/`market` at `pipeline.py:670,730`); isotonic-only, no Platt
  (`fit_calibration.py:52-86`); `MIN_FIT=400` leaves thin markets uncalibrated.
- **Readiness:** **[HAVE]** for games (fit maps exist); **[DERIVE]** for props (the
  prop-calibration walk-forwards already produce pred/outcome pairs —
  `backtest.run_mlb_prop_calibration:903`, `run_wnba_prop_calibration:1036`).
- **Sketch:** (1) fit + validate maps for all sports, flip `APPLY_CALIBRATION`; (2)
  thread `sport`/`market` into the prop `_market_eval` calls and fit prop maps from
  the existing calibration collectors; (3) add a `_platt(preds,outs)` alternative in
  `fit_calibration.py` and pick per-market by holdout Brier; (4) decide the
  calibrate-then-shrink order deliberately (calibrate first, then a *smaller*
  market seed — they partly do the same job).

---

### #4 — Time-decay recency weighting  · impact MED · effort LOW-MED
- **Technique:** exponentially weight recent games (per-sport half-life) instead of
  a flat window with a hard cutoff.
- **Impact:** MED. Flat windows throw away in-window ordering and jump
  discontinuously when a game exits the window; decay is smoother and reacts faster
  to real form changes. Lifts all off/def-rated sports (biggest where form moves
  fast: NBA/WNBA/NHL).
- **Gap:** equal-weight window in both live and backtest ratings
  (`generic.py:52-56`, `backtest.py:310-317`, `_Form.update:335-344`).
- **Readiness:** **[HAVE]** — same game list, just reweighted.
- **Sketch:** replace the plain mean in `team_ratings`/`_Form._raw` with
  `Σ wᵢ xᵢ / Σ wᵢ`, `wᵢ = 0.5**(age_days / half_life)`; add a `form_halflife`
  field to `Sport`; sweep half-life per sport on the walk-forward Brier/CLV. Must be
  changed in **both** `generic.py` and `backtest._Form` so live == backtest.

---

### #5 — Empirical-Bayes / hierarchical shrinkage priors  · impact MED · effort MED
- **Technique:** replace the fixed `0.65` constant with a shrinkage that scales with
  each estimate's uncertainty (James-Stein / empirical-Bayes), and a tier-aware /
  team-specific prior so elite and bottom teams aren't pulled to the same mean.
- **Impact:** MED. Fixed shrinkage over-regresses low-variance strong teams early
  and under-regresses noisy ones; EB fixes both. Lifts all off/def-rated sports;
  biggest for short seasons and high-turnover leagues (WNBA, NCAAF).
- **Gap:** `RATING_SHRINK = 0.65` fixed (`generic.py:26`, `config.py:82`); single
  global prior `league_ppg` (`generic.py:55`); pull strength depends only on `n`
  (`generic.py:54`).
- **Readiness:** **[DERIVE]** — the between-team variance and within-team
  (residual) variance needed for the EB weight are estimable from the same backfills.
- **Sketch:** compute `w = τ² / (τ² + σ²/n)` (τ² = between-team spread of true
  rates, σ² = per-game noise) per sport once from the backfill; store on `Sport`;
  apply in `team_ratings`/`_Form.rating`. Optional tier prior = a coarse
  preseason/Elo-implied rate instead of `league_ppg`.

---

### #6 — Per-league, data-estimated, shrunk HFA  · impact LOW-MED · effort LOW
- **Technique:** estimate home-field advantage per league from data and shrink it;
  flag neutral sites so HFA isn't applied.
- **Impact:** LOW-MED. HFA is a real but small and slowly-drifting edge (post-2020
  HFA fell across leagues); getting it modestly wrong biases every game's margin the
  same direction. Lifts all team sports.
- **Gap:** `hfa` and `elo_home_edge` are hardcoded constants
  (`sports.py`, e.g. WNBA `hfa=2.5`, MLB `hfa=0.12`, `elo_home_edge=50`), not
  re-estimated or shrunk, and there is no neutral-site flag in the generic path.
- **Readiness:** **[DERIVE]** — home-vs-away margin is directly measurable from the
  committed backfills; **[NEW DATA]** only for a neutral-site indicator (some feeds
  carry it).
- **Sketch:** a one-off `estimate_hfa(sport)` from backfill home margins, shrunk to
  a cross-league prior; write back into `Sport.hfa`/`elo_home_edge`; add a
  `neutral` flag on slate rows that zeroes HFA. Re-estimate seasonally for drift.

---

### #7 — Coherent joint margin+total & key-number-aware margin  · impact MED · effort MED
- **Technique:** model margin and total jointly (they're correlated) and use a
  key-number-aware (Gaussian-plus-spike) margin distribution for the
  discrete-scoring sports.
- **Impact:** MED, concentrated on spreads/totals. For normal-model sports the
  margin and total are currently drawn from **independent** Gaussians; and a plain
  Normal mis-prices cover/push mass at NFL ±3/±7 (research §1.2). Lifts NFL/NCAAF
  spreads most; joint-coherence helps all normal sports' totals.
- **Gap:** `prob_over` uses `sigma_total`, `home_cover_prob` uses `sigma_margin`,
  independently (`generic.py:104-117`); `with_consistent_margin` reconciles
  ML↔spread but leaves **total** independent of margin (`generic.py:186`, "Totals
  are untouched"). No key-number distribution. (Poisson sports simulate jointly, so
  they're already coherent — `_poisson_draws:203`.)
- **Readiness:** **[HAVE]** for a bivariate-normal (needs only the margin/total
  correlation, estimable from residuals); **[DERIVE]** for empirical key-number
  weights from the football backfills.
- **Sketch:** replace the two independent normals with a bivariate normal
  `(margin, total)` (add `rho` per sport from residual covariance) so a projected
  blowout correctly co-moves the total; for NFL/NCAAF add an empirical
  margin-distribution table for cover/push near key numbers (already flagged as
  Stage 3 item 7 in `ACCURACY_ROADMAP.md`).

---

### #8 — Learned ensemble weights (incl. market)  · impact MED · effort MED-HIGH
- **Technique:** stack power-rating + score-based + market with a **learned**
  meta-model (simple regularized logistic on out-of-fold base preds + the line),
  then calibrate the stack.
- **Impact:** MED. The pieces exist but are combined with **hand-tuned fixed
  weights** swept one at a time, which can't capture where each source is best and
  omits the market from the blend entirely. Lifts every sport with ≥2 signals.
- **Gap:** `elo_blend` is a fixed per-sport constant applied as a static convex
  combo (`pipeline.py:957-960`, `backtest.py:607-609`); `epa_blend` likewise; the
  market is not a member of the ensemble at projection time.
- **Readiness:** **[DERIVE]** — the walk-forward loop already generates the base
  predictions out-of-fold; a meta-learner trains on those.
- **Sketch:** collect `(p_offdef, p_elo, p_market)` per game in the walk-forward,
  fit a time-respecting regularized logistic meta-learner per sport, replace the
  fixed-`elo_blend` line, then calibrate (#3). Overlaps with #1 (market becomes a
  first-class ensemble member). Gate on lift vs the current fixed blend.

---

### #9 — Regression-to-mean with carried-over preseason priors  · impact MED (NFL/NCAAF) / LOW (else) · effort MED-HIGH
- **Technique:** seed new-season ratings from carried-over strength (prior-season
  rating, returning production, talent) instead of cold-start league average.
- **Impact:** MED for high-turnover/short-schedule sports (NCAAF, NFL early weeks,
  WNBA openers); LOW for long-season sports where form re-accumulates fast.
- **Gap:** cold start = `league_ppg` (`generic.expected_score:74-78`); only Elo
  carries anything across seasons, via a blunt fixed `season_regress`
  (`elo.py:36-42`). No returning-production prior (already acknowledged as Stage 2
  in `ACCURACY_ROADMAP.md`).
- **Readiness:** **[HAVE]** prior-season rating (in the backfill); **[NEW DATA]**
  returning production / talent (CFBD for NCAAF — client exists but unwired).
- **Sketch:** initialize each season's off/def prior from a regressed prior-season
  rate rather than `league_ppg`; for NCAAF pull CFBD returning production as the
  prior mean. Blends with #5 (the prior in the EB shrinkage).

---

### #10 — (Distribution/variance quality) — largely a STRENGTH, keep · impact — · effort —
Noted for completeness: predicting a full distribution with sport-specific σ and
over-dispersion (prompt item 6) is **already done well** — measured per-sport
`sigma_margin`/`sigma_total` (`sports.py`), NB run sim (`config.py:154`), per-market
NB props (`generic.py:236-273`). The remaining distribution gaps are the *joint*
and *key-number* refinements captured in #7, not the marginal σ.

---

## Ranked summary

| Rank | Lever | Impact | Effort | Readiness | Primary files to change |
|---|---|---|---|---|---|
| 1 | **Market-seeded projections** | HIGH | MED | HAVE | `pipeline.py` (project_* ), `odds.py` |
| 2 | **Market-baseline skill score + CRPS in eval** | HIGH | LOW | HAVE | `backtest.py:733-757` |
| 3 | **Turn calibration ON + props + Platt** | MED-HIGH | LOW | HAVE/DERIVE | `config.py:235`, `pipeline.py:670,730`, `fit_calibration.py` |
| 4 | **Time-decay recency** | MED | LOW-MED | HAVE | `generic.py:52-56`, `backtest.py:310-317` |
| 5 | **Empirical-Bayes / hierarchical shrinkage** | MED | MED | DERIVE | `generic.py:26,52-56` |
| 6 | **Per-league shrunk HFA + neutral sites** | LOW-MED | LOW | DERIVE | `sports.py` (hfa/elo_home_edge) |
| 7 | **Joint margin+total & key numbers** | MED | MED | HAVE/DERIVE | `generic.py:104-117,186` |
| 8 | **Learned ensemble weights (incl. market)** | MED | MED-HIGH | DERIVE | `pipeline.py:957-960`, `backtest.py:607-609` |
| 9 | **Carried-over preseason priors** | MED (FB) / LOW | MED-HIGH | HAVE/NEW | `generic.py:74-78`, CFBD client |

Suggested execution order deviates slightly from the raw ranking: **do #2 first**
(it's the instrument), then #1 and #3 together (the two anchoring levers, whose
interaction #2 lets you measure), then #4–#8.

---

## The central question: should projections be anchored to / blended with the market?

**Yes — anchor the *projection* to the market, but to a reference (opening/current)
line, not to the close you grade CLV against; keep the model's weight small and
tune it per market on walk-forward CLV.**

Reasoning, given the north-star is beating the close:

1. **The close is the strongest predictor**, so a projection that ignores it starts
   below a free baseline. Our own config already diagnoses the symptom of an
   un-anchored model — "far too many fat edges … over-confidence + stale inputs, not
   alpha" (`config.py:210-213`). Anchoring is the standard fix and, per research,
   "reliably beats the closing line" more often than the raw model.

2. **But anchoring to the *same* line you grade against mechanically kills CLV.**
   If projection ≈ close, then `fair_model − fair_close ≈ 0` by construction. So the
   anchor must be a line *earlier* than the grading close: seed the projection from
   the **open / current** number, let genuine signal deviate, bet into that number,
   and grade CLV as `fair_model − fair_close`. The engine already contains exactly
   this architecture in `run_mlb_clv_open_close` (`backtest.py:820`) — bet at
   open, measure open→close movement — it just isn't the projection path.

3. **This is a bias-variance sweet spot, found empirically, per market.** Weight
   `w=0` on the market = today's raw over-confident model (many false edges,
   CLV-negative on efficient markets). Weight `w=1` = you copy the line (CLV≡0,
   nothing to bet — the `config.py:222-224` observation). The optimum is the small
   `w` (research: 0.1–0.3) that removes noise while preserving real deviation. It
   differs by market: NFL spreads have already shown CLV≈0 (`ACCURACY_ROADMAP.md`) →
   near-zero model weight; props / secondary sports / smaller-league totals are less
   efficient → more model weight. Tune `w` on the open→close CLV-positive rate, not
   Brier alone.

4. **Separate the two market interactions that are currently conflated.** Today the
   *only* market term is the bet-selection shrink in `_market_eval`
   (`pipeline.py:75`), applied downstream, so the published projection and the CLV
   metric both ignore it (`pipeline.py:956-969`; `backtest.py:651`). Introduce a
   distinct **projection-time market seed** so the number we publish and grade *is*
   the posterior; then decide whether the downstream bet-selection shrink is still
   needed on top (it may collapse into the same operation) and whether calibration
   (#3) precedes it. Prerequisite for trusting any of this: the market-baseline
   skill score (#2), so we can see whether seeding actually moves CLV.

**Bottom line:** market-anchor the projection to a pre-close reference with a small,
per-market, CLV-tuned weight; measure everything against the de-vigged close as the
baseline; and never anchor to the very line you're trying to beat.
