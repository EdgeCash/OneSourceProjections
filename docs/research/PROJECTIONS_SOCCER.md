# Soccer Projection Accuracy: Gap Analysis vs. Best-in-Class

**Scope:** How our deterministic soccer model produces Home xG / Away xG → 1X2, totals,
and BTTS, and what leading systems do that we don't. Projection accuracy is the sole
objective here; wagering/EV logic (`_attach_soccer_edges`) is downstream and out of scope.

**Bottom line up front:** Our soccer path is a generic points model wearing a Dixon-Coles
hat. The scoreline *distribution* (`models/soccer.py`) is genuinely Dixon-Coles. But the
*inputs* to it — each side's expected goals — are **not** produced by a Dixon-Coles
fit. They come from the shared cross-sport `team_ratings` (goals scored/allowed, shrunk
to league mean) fed through a heuristic log5 `expected_score`. There is **no maximum-
likelihood fit of team attack/defence strengths, no time-decay weighting of recent form,
no fitted `rho`, no shot-based xG feed, and no per-league home advantage.** `rho` and the
effective team strengths are both fixed/unfit. Everything the model needs to close the
biggest gaps (time-decay, MLE fit, per-league HFA) is derivable from the results feed we
already pull; only true shot-xG requires a new data source.

---

## 1. What our model does today (grounded)

### 1.1 The scoreline distribution — `project547/models/soccer.py`
- Independent Poisson for each side with the classic **Dixon-Coles low-score
  correction** re-weighting the (0,0)/(0,1)/(1,0)/(1,1) cells (`_dc_tau`, lines 30–40).
- **`rho` is a hard-coded constant `RHO = -0.13`** (`soccer.py:20`), "a standard fitted
  value for league football, kept fixed" (comment, lines 20–21). Every function
  (`score_matrix:43`, `outcome_probs:57`, `over_prob:73`, `btts_prob:86`) takes
  `rho=RHO` as a default and is **never called with an overriding value** — the pipeline
  calls `soccer.outcome_probs(h_exp, a_exp)` with no `rho` (`pipeline.py:1010`,
  `1018`, `1019`). So `rho` is global, fixed, and identical for every league and match.
- Grid truncated at `MAX_GOALS = 12` (`soccer.py:19`). 1X2 / over / BTTS are all summed
  off the same normalised grid, so they are internally consistent. This part is correct
  and standard.

### 1.2 How each side's expected goals are actually built
The expected goals `(h_exp, a_exp)` that feed the grid are **not** produced by
`models/soccer.py`. They come from the generic engine:

`pipeline.project_soccer_games` (`pipeline.py:987–1021`):
1. Pull completed results over a lookback window: `start = date - form_days`
   (`pipeline.py:999`), `results = espn.results_range(...)` (`:1001`). For MLS
   `form_days = 120` (`sports.py:165`).
2. `ratings = generic.team_ratings(results, sport.league_ppg, sport.opponent_adjust)`
   (`pipeline.py:1005`).
3. `h_exp, a_exp = generic.expected_score(sport, home_rating, away_rating)`
   (`pipeline.py:1008`).
4. `probs = soccer.outcome_probs(h_exp, a_exp)` (`pipeline.py:1010`) plus
   `over_prob`/`btts_prob` (`:1018–1019`).

`generic.team_ratings` (`generic.py:36–66`):
- For each team, collect (goals for, goals against) per game.
- `n = len(games)`; **shrink weight `w = RATING_SHRINK * min(1.0, n/10)`** with
  `RATING_SHRINK = 0.65` (`generic.py:26,54`).
- `scored = w * (mean goals for) + (1-w) * league_ppg`, same for `allowed`
  (`generic.py:55–56`). **This is a plain unweighted mean of every game in the window —
  a match from 119 days ago counts exactly as much as last night's** (no time decay).
- `opponent_adjust` (`generic.py:60–64`) is a **one-pass** strength-of-schedule tweak,
  but it is **OFF for MLS** — the MLS `Sport` entry never sets `opponent_adjust`, so it
  defaults `False` (`sports.py:50,156–167`). So today MLS ratings carry **no
  opponent adjustment at all**.

`generic.expected_score` (`generic.py:69–88`), multiplicative branch (MLS sets
`score_method="multiplicative"`, `sports.py:166`):
```
h_exp = league * (h_off/league) * (a_def/league) + hfa/2
a_exp = league * (a_off/league) * (h_def/league) - hfa/2
```
with `league = 1.45`, `hfa = 0.32` for MLS (`sports.py:164`). Home advantage is a
**single fixed goal constant split symmetrically** (±0.16 goals per side), identical for
every team and every league, applied as an *additive* bump on top of a *multiplicative*
strength model.

### 1.3 What feeds exist
- **Results feed (ESPN):** `espn.results_range` → `_parse_events` returns
  `{game_id, date, game_time, completed, home_team, away_team, home_score, away_score}`
  (`espn.py:45–56`). **Crucially, each result carries a `date` (`espn.py:48`)** — so
  match age is available for free; time-decay is a pure derivation, not new data.
- **No shot / xG data anywhere.** `_parse_events` carries only final goals. Grep for
  `xg/xG/shots/expected` across the soccer path returns nothing. There is no
  understat/Opta/fbref client.
- **Odds feed** exists for pricing (`oddsapi.game_odds`, `_soccer_odds_index`,
  `pipeline.py:1111–1148`) but is downstream of projection and out of scope.

### 1.4 One-line summary of the gap surface
> The distribution is Dixon-Coles; the **inputs** are a generic goals-average model.
> `rho` is fixed, team strengths are method-of-moments shrunk averages (not MLE), all
> matches in the window are weighted equally, HFA is a global constant, and there is no
> shot-based xG. Only MLS is wired as a soccer league.

---

## 2. How best-in-class systems produce Home/Away xG → 1X2

**FiveThirtyEight SPI (the reference public system).** Every team carries an **offensive
rating** (goals it would score vs. an average team on a neutral field) and a **defensive
rating** (goals it would concede). A match projection combines the two teams' ratings
plus home advantage into expected goals for each side, then a scoreline model turns those
into 1X2/totals. The rating *update* is the key idea: after each match a team's rating
moves based on a **composite of four performance signals — goals (adjusted for red cards
and timing), shot-based expected goals (xG), and non-shot xG (dangerous actions near
goal)** — regressed against the opponent's rating. Because it feeds on xG rather than raw
goals, a team that wins 1–0 while being outshot *loses* rating. SPI also normalises across
leagues via a global club rating so promoted/relegated teams start at a sensible level.
*(Sources below.)*

**Understat / Opta / fbref xG.** Per-shot expected-goals models score every attempt by
its historical conversion probability (distance, angle, body part, assist type, defensive
pressure). Summing shot xG per team per match gives a **far less noisy estimate of true
scoring rate than goals** — goals are a small-sample Bernoulli draw off the underlying xG
process. Elite projection systems rate teams on rolling xG-for / xG-against, not goals.

**Dixon-Coles with time-decay + MLE (the academic/industry standard).** The original
Dixon & Coles (1997) model fits, by **maximum likelihood over all matches at once**: an
attack strength αᵢ and defence strength βᵢ per team, a home-advantage term γ, and the
low-score dependence **`rho` (fitted, not assumed)**. Recent matches are up-weighted by an
**exponential time-decay φ(t) = exp(−ξ·t)** on the log-likelihood, with ξ itself tuned by
maximising out-of-sample profile likelihood (published EPL optima around ξ ≈ 0.003/day
over multi-season windows). Modern variants swap goals for xG in the likelihood
("Dixon-Coles + xG"). Typical fitted `rho` magnitudes are ~0.05–0.15.

**Club Elo / Elo-style.** Continuous goal-difference-aware Elo with a home-edge term and
between-season regression — strong, cheap strength-of-schedule signal that carries across
seasons (we already do exactly this for WNBA/NBA/NFL/NHL via `models/elo.py`, but **not
for soccer**).

**Infogol / sharp market.** Infogol is explicitly an **xG-driven** model (rolling
shot-xG ratings → Poisson scoreline). The sharp closing line is the single most accurate
public probability estimate and is used as ground truth / a shrink target — which our
pricing layer already does (`blend_toward_market`), but that helps EV, not the raw
projection.

Common threads the best systems share and we lack: **(1) shot-xG inputs**, **(2) attack/
defence strengths fit by MLE with league context**, **(3) time-decay weighting**,
**(4) per-league home advantage**, **(5) fitted `rho`**, **(6) cross-league / promotion
normalisation**, and to a lesser degree **(7) fixture congestion / rest**.

---

## 3. Gap analysis (ranked by impact ÷ effort)

Data-readiness tags: **[HAVE]** = already in a feed we pull; **[DERIVE]** = computable
from existing feeds with new code; **[NEW DATA]** = requires a source we don't ingest.

| # | Technique | Impact | Effort | Data | Rank |
|---|-----------|--------|--------|------|------|
| 1 | Time-decay weighting of recent matches | **High** | Low | [DERIVE] results `date` | ★★★★★ |
| 2 | Fit team attack/defence + `rho` + HFA by MLE (real Dixon-Coles) | **High** | Med | [DERIVE] | ★★★★☆ |
| 3 | Per-league home advantage (and asymmetric application) | Med | Low | [HAVE] `Sport.hfa` | ★★★★☆ |
| 4 | Turn on opponent-adjust / add soccer Elo strength-of-schedule | Med | Low | [HAVE]/[DERIVE] | ★★★★☆ |
| 5 | Shot-based xG inputs (rate teams on xG, not goals) | **High** | High | [NEW DATA] understat/fbref/Opta | ★★★☆☆ |
| 6 | Cross-league normalisation & promotion/relegation priors | Med | Med | [NEW DATA]/[DERIVE] | ★★☆☆☆ |
| 7 | Fixture congestion / rest & travel | Low | Low | [DERIVE] `date` | ★★☆☆☆ |
| 8 | League-specific `league_ppg` / scoring environment per competition | Low | Low | [HAVE] | ★★☆☆☆ |

### Lever 1 — Time-decay weighting *(High impact, Low effort, [DERIVE])*
- **Technique:** Weight each historical match by φ(t)=exp(−ξ·Δdays) so recent form
  dominates; this is the single highest-ROI addition to any goals-based football model.
- **Impact — High:** Form and squad quality drift within a 120-day MLS window; treating a
  match from 17 weeks ago equally with last week's demonstrably degrades calibration.
  This is the classic Dixon-Coles improvement and moves both xG inputs and therefore
  every downstream market (1X2/totals/BTTS).
- **Our gap:** `generic.team_ratings` uses a **flat mean** — `w*(sum/n)` with no per-game
  weight (`generic.py:54–56`). The `date` needed is already on every result
  (`espn.py:48`) and already fetched, so nothing new is required.
- **Data:** [DERIVE] from `results[*]["date"]`.
- **Sketch:** Add optional `as_of`/`decay` args to `team_ratings` (`generic.py:36`):
  compute `wt = exp(-xi * days_ago)` per game and replace the simple mean with a
  weighted mean (`scored = Σ wt·gf / Σ wt`). Pass a soccer `decay_xi` through
  `Sport` (`sports.py`) and from `project_soccer_games` (`pipeline.py:1005`). Tune ξ by
  walk-forward log-loss on MLS 1X2. Keep flat behaviour (`xi=0`) as the default for the
  other sports so nothing else changes.

### Lever 2 — Real Dixon-Coles MLE fit (attack/defence + `rho` + HFA) *(High impact, Med effort, [DERIVE])*
- **Technique:** Fit αᵢ (attack), βᵢ (defence), γ (home), and `rho` jointly by maximising
  the (time-weighted) Dixon-Coles log-likelihood over all matches, instead of deriving
  expected goals from shrunk goal averages fed through a log5 heuristic.
- **Impact — High:** The current pipeline never fits anything — ratings are shrunk means
  (`generic.py:55`) and `expected_score` is a fixed algebraic form (`generic.py:83–84`).
  A joint MLE captures each opponent's specific attack×defence interaction and the true
  low-score dependence, which the generic engine only approximates. This is what
  "best-in-class Dixon-Coles" means.
- **Our gap:** **`rho` and team strengths are both currently fixed/unfit.** `rho=-0.13`
  is a literature constant (`soccer.py:20`); team strengths are method-of-moments, not
  likelihood-fit; `expected_score` is a heuristic, not a model fit.
- **Data:** [DERIVE] from the same results feed — pure goals + dates, which we already
  have.
- **Sketch:** New `models/soccer.py::fit_strengths(results, decay_xi) -> {attack, defence,
  home_adv, rho}` using `scipy.optimize.minimize` on the DC negative log-likelihood
  (reuse `_pois_pmf`/`_dc_tau`, `soccer.py:24–40`, for the per-match terms; apply the
  Lever-1 weights). Then `expected_goals(fit, home, away) -> (h_exp, a_exp)` with
  `h_exp = exp(attack[h] + defence[a] + home_adv)`. In `project_soccer_games`
  (`pipeline.py:1005–1010`) branch to the fitted path and pass the **fitted `rho`** into
  `outcome_probs(h_exp, a_exp, rho=fit.rho)` (today `rho` is dropped, `:1010`). Gate
  behind a config flag and validate walk-forward before flipping MLS over — mirror the
  EPA/Elo "validated before prod" pattern already used elsewhere (`generic.py:152–160`).

### Lever 3 — Per-league home advantage *(Med impact, Low effort, [HAVE])*
- **Technique:** HFA varies materially by league (MLS travel/altitude is unusually high)
  and is better estimated from data than assumed; also, home advantage is not perfectly
  symmetric between the attack and defence sides.
- **Impact — Med:** Directly shifts the 1X2 split and the home/away goal balance. MLS's
  0.32 constant (`sports.py:164`) is a reasonable guess but unvalidated and shared with no
  other league (none exist yet), and applied as ±hfa/2 symmetrically (`generic.py:83–84`).
- **Our gap:** HFA is a hand-set constant, symmetric, additive-on-multiplicative.
- **Data:** [HAVE] — it's already a `Sport` field; better still, fall out of the Lever-2
  MLE as the fitted γ.
- **Sketch:** Short term, back the MLS `hfa` out of the walk-forward home-goal residuals.
  Long term, let the fitted γ from Lever 2 replace the constant so HFA is estimated
  per-league from that league's own results.

### Lever 4 — Opponent adjustment / soccer Elo *(Med impact, Low effort, [HAVE]/[DERIVE])*
- **Technique:** Correct raw goal rates for schedule strength; or maintain a soccer Elo to
  carry cross-season, opponent-aware strength.
- **Impact — Med:** Early-window and unbalanced-schedule teams are mis-rated without it.
- **Our gap:** `opponent_adjust` exists (`generic.py:60–64`) but is **off for MLS**
  (`sports.py:156–167`), and `elo_blend` is 0 for MLS — the whole `models/elo.py`
  machinery that helps NBA/WNBA/NFL/NHL (`pipeline.py:919–933`) is simply not wired into
  the soccer path (`project_soccer_games` has no Elo block).
- **Data:** [HAVE] `opponent_adjust` flag; [DERIVE] Elo from results.
- **Sketch:** Cheapest first step — flip `opponent_adjust=True` on the MLS `Sport` and
  walk-forward it (this is a one-line experiment). If Lever 2 lands, its MLE already
  encodes opponent strength jointly, making this redundant; treat it as a bridge.

### Lever 5 — Shot-based xG inputs *(High impact, High effort, [NEW DATA])*
- **Technique:** Rate teams on rolling **xG-for / xG-against** (from a per-shot model)
  instead of goals; goals are a noisy sample of the underlying xG process.
- **Impact — High** on true skill estimation, but **gated by data**: this is the biggest
  single accuracy lever the top systems have and we entirely lack — yet it needs a feed we
  don't ingest, and MLS xG coverage is thinner than Big-5 European leagues.
- **Our gap:** No xG anywhere; `_parse_events` carries only final goals (`espn.py:53–54`).
- **Data:** **[NEW DATA]** — an xG source: understat (free, decent coverage incl. MLS),
  fbref/StatsBomb, or Opta (paid, best). Need a new client analogous to
  `clients/statcast.py`, keyed by team+date and normalised to our team names
  (`names.normalize`).
- **Sketch:** New `clients/understat.py` → per-team match xG. Blend xG into the rating:
  use xG-for/against in place of (or blended with, e.g. 70/30) goals inside
  `team_ratings`/the MLE likelihood. This is the highest ceiling but should follow Levers
  1–2 because it multiplies their value and carries integration + coverage risk. Rank it
  below the derivations precisely because impact÷effort is lower despite high impact.

### Lever 6 — Cross-league normalisation & promotion/relegation *(Med, Med, [NEW DATA]/[DERIVE])*
- Only relevant once we add more than one soccer league. Promoted teams have no
  same-league history; a cross-league prior (or a global Elo like Club Elo) seeds them.
  Not actionable while MLS is the only wired league (`sports.py`), but a prerequisite for
  expanding to EPL/Big-5 where the accuracy payoff (and data availability) is largest.

### Lever 7 — Fixture congestion / rest & travel *(Low, Low, [DERIVE])*
- Midweek games, short rest, and long travel measurably depress expected goals. We already
  compute rest days for the generic sports (`pipeline.py:944–964`) but the soccer path
  doesn't. [DERIVE] from result dates. Low standalone impact for MLS; fold in only after
  Levers 1–4.

### Lever 8 — Per-competition scoring environment *(Low, Low, [HAVE])*
- `league_ppg` is a single knob per `Sport` (`sports.py:164`). Fine for MLS-only; becomes
  a cheap correctness item when multiple leagues (different base scoring rates) are added.

---

## 4. Explicit flags
- **`rho` is fixed and unfit.** `RHO=-0.13` is a hard-coded constant
  (`soccer.py:20`); it is never fit and never varies by league or match. Best practice is
  to fit it by MLE (Lever 2).
- **Team strengths are unfit.** They are shrunk goal averages (`generic.py:55–56`) fed
  through a fixed log5 form (`generic.py:83–84`), not maximum-likelihood attack/defence
  parameters. There is no optimisation step anywhere in the soccer path.
- **No time-decay:** every match in the 120-day window is weighted equally
  (`generic.py:54–56`).
- **No shot-xG:** the "xG" in this pipeline is expected *goals from a Poisson mean*, not
  shot-based xG. The results feed carries goals only (`espn.py:53–54`).
- **Home advantage** is a single symmetric additive constant (`generic.py:83–84`,
  `sports.py:164`), not per-league or data-fit.
- **Opponent adjustment and Elo** are available in the codebase but **not enabled for the
  soccer path** (`sports.py:156–167`; `project_soccer_games` has no Elo block).

---

## 5. Recommended sequence (impact ÷ effort)
1. **Time-decay weighting** (Lever 1) — highest ROI, pure derivation, one function.
2. **Real Dixon-Coles MLE fit** with fitted `rho`, γ, and time-decay baked in
   (Levers 2+3) — the structural fix; also delivers per-league HFA for free.
3. **Opponent-adjust flip / soccer Elo** (Lever 4) — one-line experiment; bridge until
   the MLE subsumes it.
4. **Shot-xG feed** (Lever 5) — highest ceiling, but new data + coverage risk; do it after
   the derivations so it compounds their value.
5. **Cross-league normalisation + rest** (Levers 6–7) — activate when expanding beyond MLS.

Every code lever above is validation-gated the same way EPA/Elo already are
(`generic.py:152–160`): implement behind a flag, prove it walk-forward on MLS 1X2/totals
log-loss and calibration, then flip the `Sport`.

---

### Sources
- [How Our Club Soccer Projections Work — FiveThirtyEight](https://fivethirtyeight.com/features/how-our-club-soccer-projections-work/)
- [How Our 2022 World Cup Predictions Work — FiveThirtyEight](https://fivethirtyeight.com/features/how-our-2022-world-cup-predictions-work/)
- [How Good Really Was FiveThirtyEight's Soccer Power Index? — transferscience.com](https://www.transferscience.com/p/how-good-really-was-fivethirtyeights)
- [Predicting Football Results With Dixon-Coles and Time-Weighting — dashee87.github.io](https://dashee87.github.io/football/python/predicting-football-results-with-statistical-modelling-dixon-coles-and-time-weighting/)
- [The Dixon-Coles approach to time-weighted Poisson regression — opisthokonta.net](https://opisthokonta.net/?p=1013)
- [Dixon-Coles and xG: together at last — statsandsnakeoil.com](https://www.statsandsnakeoil.com/2018/06/22/dixon-coles-and-xg-together-at-last/)
- [Dixon-Coles Model Explained — predictionengine.app](https://predictionengine.app/learn/dixon-coles-soccer-model)
