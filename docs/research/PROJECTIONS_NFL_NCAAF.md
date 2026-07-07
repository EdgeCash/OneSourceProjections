# Projecting NFL & NCAAF: Team-A points / Team-B points / total — gap analysis

**Scope:** projection *accuracy* of each side's points and the game total. Wagering
logic (devig, Kelly, CLV gates) is downstream and explicitly out of scope here. Where
the existing `docs/ACCURACY_ROADMAP.md` optimized for win-prob/CLV and concluded a
lever "doesn't beat the close," that verdict does **not** settle whether the lever
improves the published points/total — a distinction this report makes repeatedly,
because several high-value points/total levers were shelved on a *betting* metric.

All code claims are grounded in files read June/July 2026.

---

## STEP 1 — How we produce points-for / points-against / total today

The NFL/NCAAF game projection call site is `pipeline.project_generic_games`
(`project547/pipeline.py:906`). Both sports run the identical generic normal-model path;
MLB/NHL/soccer use their own richer pipelines. The chain:

1. **Team ratings** — `generic.team_ratings` (`project547/models/generic.py:36`). For each
   team it takes points scored/allowed over the last `form_days` games and shrinks
   toward `league_ppg`: weight `w = RATING_SHRINK(0.65) × min(1, n/10)` on the observed
   rate, remainder on the league prior (`generic.py:52-56`). Result is a `TeamRating`
   with `scored` and `allowed` (points per game). `form_days = 140` for both NFL and
   NCAAF (`sports.py:126,142`) — essentially the whole season, equal-weighted, **no
   recency decay and no cross-season carryover** for the points ratings (Elo carries
   season-to-season; the points ratings cold-start at league average every year).

2. **Opponent adjustment (SoS) — present in code but OFF for football.**
   `team_ratings(opponent_adjust=True)` applies a one-pass SoS correction
   (`generic.py:60-64`): facing weak defenses discounts your offense, facing strong
   offenses credits your defense. **NFL and NCAAF both leave `opponent_adjust` at its
   `False` default** (`sports.py:119-147`) — only NBA and NHL turn it on
   (`sports.py:117,176`). So football's points ratings are **raw, schedule-unadjusted
   PPG.** A team that fattened up on weak defenses is projected to keep scoring.

3. **Expected score** — `generic.expected_score` (`generic.py:69`). Both football sports
   use `score_method="multiplicative"` (`sports.py:127,144`), the "log5-for-points" form
   (`generic.py:83-84`):
   ```
   h_exp = league × (h_off/league) × (a_def/league) + hfa/2
   a_exp = league × (a_off/league) × (h_def/league) − hfa/2
   ```
   HFA is `1.8` (NFL) / `2.7` (NCAAF) points (`sports.py:125,141`), split half to each
   side. A floor of `league×0.3` prevents non-positive scores (`generic.py:88`).

4. **Total** = `h_exp + a_exp` (`generic.py:147`), i.e. the sum of the two expected
   scores. **The total is a pure by-product of the two point estimates.** Nothing
   downstream ever adjusts it.

5. **Margin & distribution** — `project_game` sets `margin_mean = h_exp − a_exp` and the
   home win prob from `Normal(margin_mean, sigma_margin)` (`generic.py:138-140`). P(over)
   is `1 − Normal.cdf(line, total_mean, sigma_total)` (`generic.py:104-106`).
   `sigma_margin/sigma_total` are **fixed constants**: NFL 16.0/13.5, NCAAF 16.0/16.5
   (`sports.py:125,141`) — no game-specific variance.

6. **Elo + rest, margin only.** When `elo_blend>0` (NFL 0.60, NCAAF 0.50) the Elo home
   win prob is blended in (`pipeline.py:957-960`); `rest_coeff` nudges the win prob by
   rest-day differential (NFL 0.5, NCAAF **0.0** — off) (`pipeline.py:961-964`,
   `sports.py:131`). The blended win prob is folded back into the **margin** via
   `with_consistent_margin` (`generic.py:173`, `pipeline.py:968`) so spread and moneyline
   agree. **Crucially, Elo and rest move only the margin — `total_mean` is untouched by
   design** (`generic.py:184-186` docstring; totals never re-derived). All the model's
   sophistication improves the margin/win side; the **total remains the crude
   schedule-unadjusted midpoint sum.**

7. **What is NOT wired.** `epa_blend=0` for both (`sports.py`), so the EPA engine
   (`project547/epa.py`) and the nflverse PBP loader (`clients/nflverse.py`) do not touch
   live projections. `qb_coeff=0` (`sports.py:56`). The CFBD client
   (`clients/cfbd.py` — SP+, FPI, PPA, returning production, talent, lines) is **entirely
   unused** by the NCAAF path. There is **no weather** for football (`weather.py` is
   MLB-park-only, `weather.py:1`), **no pace/plays model**, and **no neutral-site
   handling**: the ESPN slate parser drops `neutralSite`/venue
   (`clients/espn.py:28-57` keeps only teams/score/time), so `project_generic_games`
   applies HFA to every game including bowls and international games.

**One-line summary of today's total:** `total = league × [(h_off·a_def)+(a_off·h_def)]/league²`
from raw, unadjusted, non-recency-weighted season PPG, with **zero** opponent, pace,
weather, QB, rest, or venue correction.

---

## STEP 2 — What best-in-class systems do (projection mechanics)

**nfelo (NFL).** Elo backbone with an nflverse **EPA** power-rating prior, an
autocorrelation-corrected MOV multiplier `ln(margin+1)·(2.2/(0.001·Δelo+2.2))`, an
explicit **QB adjustment** (rolling QB value; large swing on starter change/injury),
modeled HFA, bye/rest, and it **shrinks to the market**. Projects a spread and a
distribution honoring key numbers.

**FiveThirtyEight Elo/QBElo (NFL).** Team Elo + a per-QB Elo running value; the QB term
is the headline innovation — a team's strength shifts materially when the starter
changes. MOV multiplier + season regression + travel/rest HFA.

**Inpredictable.** Win-probability-added / EPA-based team ratings, opponent-adjusted,
with a market-anchored win/spread distribution.

**nflfastR-style EPA & success-rate models.** The public-analytics consensus: rate
offense/defense on **opponent-adjusted EPA per play** and **success rate**, garbage-time
filtered, **offense estimated more reliably than defense** (weight offense ~1.5–1.6×).
Points are treated as *downstream, noisier* realizations of EPA. Projected points then
come from **efficiency × opportunity (plays)** — the pace layer.

**DVOA (Football Outsiders / FTN).** Play-by-play success vs a situation baseline
(down, distance, field position, opponent), opponent-adjusted, split offense / defense /
special teams, with separate pass vs rush efficiency.

**Massey-Peabody.** Possession-efficiency power ratings emphasizing recent play and
margin, opponent-adjusted, deliberately market-independent.

**SP+ / FEI / F+ (college).** SP+ = opponent-adjusted per-play efficiency (an EPA
cousin) with explicit **preseason priors** (returning production is the strongest
preseason signal, plus recruiting/portal **talent** and recent program history) that
decay as the season's on-field data accumulates. FEI is drive-based efficiency. F+
combines them. College demands these priors because **schedules are disjoint** (talent
connects conferences that never play) and **early-season samples are tiny**.

**The sharp market.** The single most accurate "model" is the closing line itself. Best
systems shrink toward it and grade on CLV. (Relevant to us only as a *baseline* for
points/total accuracy, not as a projection input for this scope.)

**The football scoring distribution — the part smooth Normals get wrong.** Football
margins are **not** Gaussian: probability **spikes at key numbers ±3 and ±7** (and ±10,
±14, ±6), because scoring comes in 3s and 7s. A plain Normal misprices the mass on and
around 3 and 7 by several points of probability. Best practice models the margin as a
**Gaussian-plus-spikes / empirical mixture**, and models **margin and total coherently**
(they are correlated: blowouts change late-game pace and garbage time, which moves the
total). Totals themselves are roughly Normal but **heteroskedastic** — variance grows
with the projected total and with pace.

**College-specific.** Enormous **talent gaps → blowout variance** (margin SD far above
the NFL's; a top-5 team vs a cupcake can go anywhere from +30 to +60), heavy
**garbage-time** distortion (starters pulled; efficiency stats need a WP filter),
**pace extremes** (Air Raid ~80+ plays vs ball-control ~60), FCS opponents (a
replacement-level wall, not a real rating), and frequent **neutral sites** (bowls,
kickoff games) where HFA should be zeroed.

---

## STEP 3 — Gap analysis (ranked by impact ÷ effort)

Data-readiness tags: **[HAVE]** = code/feed already in repo; **[DERIVE]** = computable
from feeds we already ingest; **[NEW DATA]** = needs a new feed (named).
Impact is on **points/total projection accuracy** specifically.

### Ranked table

| # | Lever | Impact | Effort | Data | NFL / NCAAF |
|---|---|---|---|---|---|
| 1 | **Opponent-adjust the points ratings (SoS)** | High | XS | [HAVE] | both |
| 2 | **Opponent-adjust & correct the TOTAL, not just margin** | High | S | [DERIVE] | both |
| 3 | **NCAAF preseason priors (returning prod + talent + SP+) & FCS/neutral handling** | High (NCAAF) | M | [HAVE feed: CFBD] | NCAAF |
| 4 | **Recency-weight + cross-season carryover for points ratings** | Med-High | XS | [HAVE] | both |
| 5 | **Neutral-site HFA zeroing** | Med | XS | [DERIVE: ESPN] | both (NCAAF-heavy) |
| 6 | **Pace / plays-per-game layer (efficiency × plays)** | Med-High | M | [DERIVE→NEW] | both (NCAAF>NFL) |
| 7 | **EPA-backed efficiency ratings feeding points** | Med | M | [HAVE engine] | both |
| 8 | **Weather (wind/precip) on totals** | Med | S | [HAVE pattern] | both (outdoor) |
| 9 | **Key-number margin distribution (±3/±7 spikes)** | Med | M | [DERIVE] | both |
| 10 | **Heteroskedastic / mismatch-scaled sigma (blowout variance)** | Med | S | [DERIVE] | NCAAF>NFL |
| 11 | **QB adjustment (starter change/injury) on that side's points** | High (NFL) | L | [NEW DATA: depth chart] | NFL |
| 12 | **Rest / short-week / Thursday effect on totals** | Low-Med | S | [DERIVE] | both |

---

### 1. Opponent-adjust the points ratings (SoS) — **top lever, near-zero effort**
- **Technique:** discount offense for weak defenses faced, credit defense for strong
  offenses faced. Every best-in-class system does this; raw PPG "mostly measures who you
  played."
- **Impact — High.** This is the largest cheap error in *both* point estimates and the
  total. Un-adjusted PPG systematically over-projects teams with soft schedules and
  under-projects teams from tough ones — a bias that compounds in the total (both sides
  wrong the same direction).
- **Our gap:** the correction is already coded and used by NBA/NHL but disabled for
  football (`generic.py:60-64` active only when `opponent_adjust=True`;
  `sports.py:119-147` leaves it `False` for NFL & NCAAF).
- **Data:** [HAVE].
- **Sketch:** set `opponent_adjust=True` on the NFL and NCAAF `Sport` entries
  (`sports.py:119,133`). Validate on the game backtest (total residual RMSE + margin MAE)
  before committing — one-pass SoS is crude vs a full ridge, but it is a strict
  improvement over none for totals and is free. Consider iterating the one-pass loop 2–3×
  for convergence (small edit to `generic.py:58-65`).

### 2. Opponent-adjust and correct the TOTAL, not just the margin — **the structural blind spot**
- **Technique:** the total deserves the same schedule/efficiency correction the margin
  gets. Today Elo/rest/EPA improve only `margin_mean`; `total_mean` is frozen as the raw
  midpoint sum (`generic.py:184-186`, `pipeline.py:965-968`).
- **Impact — High for totals.** Our most-refined machinery never touches the number half
  our market (totals) depends on. Even a same-quality opponent adjustment applied to the
  *sum* would remove a systematic bias documented in our own config: NCAAF `league_ppg`
  had to be hand-recentred 28.0→27.0 because the model "systematically over-projected
  totals ~2–3 pts" (`sports.py:133-139`) — a symptom of an uncorrected total, patched
  with a league constant instead of per-game adjustment.
- **Our gap:** no total-side adjustment path exists.
- **Data:** [DERIVE] (falls out of #1/#7).
- **Sketch:** after computing `h_exp/a_exp` with SoS-adjusted ratings, keep
  `total = h_exp + a_exp` but ensure the adjustment flows through both terms (it does once
  #1 is on). Longer term, add a `total_mean` adjustment hook parallel to
  `with_consistent_margin` so a pace/weather delta can shift the total independently of the
  margin (new small function in `generic.py`; call it in `pipeline.py` after line 968).

### 3. NCAAF preseason priors + FCS/neutral handling — **the college unlock**
- **Technique:** SP+ leans hard on **returning production** (top preseason signal) and
  **talent** (recruiting + portal) to seed ratings, decaying to on-field data as the
  season runs; collapse FCS opponents to one replacement-level rating; connect disjoint
  conferences via talent.
- **Impact — High for NCAAF.** Our NCAAF points ratings cold-start at `league_ppg=27.0`
  every August (no carryover, `form_days=140`), so early-season projections are near
  league-average mush for teams that are actually elite or terrible — the worst points
  errors of the year. FCS games pollute ratings (a 63-3 win over an FCS team inflates
  offense).
- **Our gap:** CFBD client exists (`clients/cfbd.py`: `sp_ratings`, `returning_production`,
  `talent`, `team_ppa`, `lines`) but is **not called anywhere** in the NCAAF path.
- **Data:** [HAVE feed] — CFBD, free key (`CFBD_API_KEY`).
- **Sketch:** in `project_generic_games` for `NCAAF`, replace the cold-start league prior
  in `generic.team_ratings` with a CFBD-derived preseason `TeamRating` (map SP+
  off/def + returning production to a points prior), and shrink on-field PPG toward *that*
  prior instead of `league_ppg` (extend `team_ratings` to accept a per-team prior dict).
  Add an FCS bucket: any opponent not in the FBS set collapses to a single
  replacement-level rating before adjustment.

### 4. Recency weighting + cross-season carryover for points ratings — **cheap accuracy**
- **Technique:** weight recent games more; carry a regressed prior-season rating into the
  new season instead of resetting to league average.
- **Impact — Med-High.** `form_days=140` equal-weights the whole season and starts each
  year at the mean. A team that changed materially mid-season (or is simply better than
  average) is mis-projected early and slow to update.
- **Our gap:** `team_ratings` has no time decay (`generic.py:52-56`) and no seed from last
  year (Elo carries over, the points ratings don't).
- **Data:** [HAVE].
- **Sketch:** add an exponential-decay weight by game age in `generic.team_ratings`
  (`generic.py:52`), and seed the shrink target with a regressed prior-year rating (like
  `Elo._maybe_regress`, `elo.py:36`). Validate offset vs current on the backtest.

### 5. Neutral-site HFA zeroing — **correctness bug, trivial fix**
- **Technique:** don't apply home-field points at neutral sites (bowls, kickoff/London/
  Dublin games, conf championships).
- **Impact — Med** (concentrated on a handful of high-visibility NCAAF/NFL games where we
  currently mis-place `hfa` points on each side and skew the margin).
- **Our gap:** ESPN gives `competition.neutralSite`, but the slate parser drops it
  (`clients/espn.py:28-57`), and `expected_score` always adds `±hfa/2` (`generic.py:83-87`).
- **Data:** [DERIVE] — already in the ESPN payload.
- **Sketch:** surface `neutralSite` (and `venue.indoor`) in `_parse_events`
  (`espn.py:45-56`); thread a `neutral` flag into `project_game`/`expected_score` and set
  `hfa=0` when true.

### 6. Pace / plays-per-game layer — **decompose points into efficiency × opportunity**
- **Technique:** `points ≈ efficiency-per-play × plays`, with a neutral-pace estimate per
  team and a game pace = f(both teams). Best models separate *how well* from *how often*.
- **Impact — Med-High**, especially NCAAF where pace ranges ~60→85 plays. Two efficient
  fast teams should project a high total; two ball-control teams a low one — the current
  log5-points form cannot express this (it has no plays term).
- **Our gap:** none of it exists in the live path; `epa.PLAYS_PER_GAME` is a static
  constant (`epa.py:41`), not a per-team rating.
- **Data:** [DERIVE] from nflverse PBP (play counts) / CFBD; [NEW DATA] to wire the feed
  live per slate.
- **Sketch:** compute per-team neutral plays/game from PBP; project game plays; multiply by
  EPA/play (see #7) to get each side's points. This is a new efficiency×pace scorer in
  `generic.py` (or a football-specific `models/football.py`), gated on backtest.

### 7. EPA-backed efficiency ratings feeding points — **the analytics backbone, re-scoped to totals**
- **Technique:** opponent-adjusted EPA/play + success rate as the offense/defense signal,
  converted to points (`epa.epa_to_points`, `epa.py:217`).
- **Impact — Med for points/total.** *Important nuance:* the roadmap found aggregate EPA
  gave "no lift" — but that verdict was on **win-probability/margin Brier & CLV**
  (`ACCURACY_ROADMAP.md:39-43,94-116`). It was **never evaluated on points-for/against or
  total residual**, which is this report's target. EPA's cleaner offense/defense split and
  garbage-time filter (`epa.py:50`) plausibly improve each *side's* points and the total
  even where it doesn't move the margin. Worth a dedicated points/total backtest before
  dismissing.
- **Our gap:** engine built (`epa.py`, `clients/nflverse.py`) but `epa_blend=0`; and no
  one has scored it on points/total.
- **Data:** [HAVE engine]; needs live PBP wired per slate for production.
- **Sketch:** extend `scripts/validate_epa.py` to also report total-RMSE and per-side
  points-MAE (not just Brier/ATS). If EPA→points beats raw PPG on those, blend it into
  `h_exp/a_exp` via the existing `with_epa_margin` pattern generalized to scores
  (`generic.py:152`).

### 8. Weather (wind/precip) on totals — **free, proven direction**
- **Technique:** strong wind depresses passing & field-goal accuracy → lower totals;
  heavy precipitation → more runs, lower scoring. Temperature is minor.
- **Impact — Med** on outdoor-game totals (wind >15 mph is the big one).
- **Our gap:** no football weather; `weather.py` is MLB-park-only (`weather.py:1`).
- **Data:** [HAVE pattern] — the Open-Meteo client + `cached_json` plumbing already exists
  (`weather.py`); [NEW DATA] = football venue coordinates + indoor/dome flag.
- **Sketch:** add an NFL/NCAAF stadium lat/lon+dome table (mirror `weather.PARKS`),
  fetch game-time wind/precip, and apply a totals multiplier (dome ⇒ no-op) to
  `total_mean` in `project_generic_games`. Keep it to totals; margin effect is second-order.

### 9. Key-number-aware margin distribution (±3, ±7) — **distribution realism**
- **Technique:** replace the plain `Normal(margin, sigma_margin)` with a Gaussian +
  empirical spikes at ±3/±7 (±6/±10/±14). This does not move the point estimate but makes
  cover/margin **probabilities** correct where most of the mass sits.
- **Impact — Med** (probability accuracy, not the mean). Also on the roadmap
  (`ACCURACY_ROADMAP.md:139-141`) but unbuilt.
- **Our gap:** `home_cover_prob` is a smooth Normal (`generic.py:111-116`).
- **Data:** [DERIVE] from the committed NFL history margins.
- **Sketch:** fit an empirical margin-spike mixture offline; swap the cover CDF in
  `generic.home_cover_prob` (`generic.py:111`) behind a flag.

### 10. Heteroskedastic / mismatch-scaled sigma — **college blowout variance**
- **Technique:** margin/total variance grows with mismatch and pace; a 30-point favorite's
  margin SD is far larger than a pick'em's. Best college models scale variance with the
  projected margin.
- **Impact — Med, NCAAF-heavy.** Fixed `sigma_margin=16.0` (`sports.py:141`) is too tight
  for blowouts and too loose for tight games, mis-calibrating win/cover probabilities.
- **Our gap:** constant sigmas.
- **Data:** [DERIVE] from backtest residuals binned by projected margin/pace.
- **Sketch:** make `sigma_margin`/`sigma_total` a function of `|margin_mean|` (and pace,
  once #6 lands) instead of a constant; thread through `prob_over`/`home_cover_prob`
  (`generic.py:104,111`).

### 11. QB adjustment on that side's points (NFL) — **highest single lever, but gated on data**
- **Technique:** shift a team's offensive points when the starting QB changes (injury/
  benching), from rolling opponent-adjusted pass-EPA+CPOE (`epa.passer_epa_ratings`,
  `epa.py:159`).
- **Impact — High for NFL points** on affected games (a backup starting can move a team's
  expected points several points). **Re-scope note:** the roadmap validated this improves
  the projection (Brier 0.2254→0.2244) but shelved it because it **hurt CLV**
  (`ACCURACY_ROADMAP.md:64-116`). For *points/total accuracy* (our scope) it is a clear
  win — the market-efficiency objection is a wagering concern, not a projection one.
- **Our gap:** `qb_coeff=0` (`sports.py:56`); passer ratings built but unused live.
- **Data:** [NEW DATA] — a projected-starter/depth-chart feed (ESPN), noted as blocked in
  this environment (`ACCURACY_ROADMAP.md:90-92`).
- **Sketch:** once a starter feed exists, maintain rolling per-QB EPA, convert the
  starter's EPA-vs-team-baseline delta to points, and add it to that side's `h_exp/a_exp`
  in `project_generic_games`. NFL only (NCAAF starter data is thinner).

### 12. Rest / short-week / Thursday on totals — **minor polish**
- **Technique:** short-week and Thursday games tend to score slightly lower; rest affects
  quality more than quantity.
- **Impact — Low-Med.** NFL `rest_coeff=0.5` already nudges the **margin**
  (`sports.py:131`) but nothing touches the **total**; NCAAF `rest_coeff=0`.
- **Data:** [DERIVE] — `last_played` rest is already computed (`pipeline.py:937-949`).
- **Sketch:** add a small totals adjustment for short-week/Thursday in
  `project_generic_games` using the existing `_rest` values; validate the sign/size on the
  backtest before enabling.

---

## NFL vs NCAAF — where they diverge

- **NCAAF-specific and higher-impact:** #3 (preseason priors + FCS/neutral — college has
  no cross-season carryover and disjoint schedules), #5 (neutral sites are common: bowls,
  kickoff games), #6 (pace extremes are far wider), #10 (blowout variance is much larger).
  College is the harder sport and where the cheap points/total wins are biggest.
- **NFL-specific:** #11 (QB adjustment — NFL has clean per-QB EPA and the QB swing is the
  league's biggest single points mover; NCAAF QB data is thinner and talent gaps dominate).
- **Shared, do first for both:** #1, #2, #4, #5.

## Cross-cutting observations

1. **Totals are the neglected output.** Every downstream refinement (Elo, rest, and the
   planned EPA) targets margin/win; the total is the raw schedule-unadjusted sum
   (`generic.py:147,184-186`). Fixing that (#1, #2) is the highest accuracy-per-effort work
   in this report and needs no new data.
2. **"No CLV edge" ≠ "no projection value."** The roadmap correctly shelved EPA/QB for
   *betting* because the NFL close is efficient (`ACCURACY_ROADMAP.md:94-116`). But the
   mission here is projection accuracy, on which those levers (#7, #11) were never scored.
   Add points-MAE / total-RMSE to `scripts/validate_epa.py` and re-judge on the right
   metric.
3. **A league constant is standing in for a per-game adjustment.** The NCAAF
   `league_ppg` 28→27 recentring (`sports.py:133-139`) is a global patch for a bias that
   opponent/pace adjustment would fix per-game.

---

## Recommended sequence (accuracy-first, each gated on the backtest)

1. Turn on `opponent_adjust` for NFL & NCAAF (#1) — flip a flag, validate. *(This alone
   fixes the biggest cheap bias in both points and totals.)*
2. Add points-MAE/total-RMSE to the EPA validator and re-score EPA & QB on points/total
   (#7, #11 re-judged) — decide what actually improves the number.
3. NCAAF preseason priors + FCS/neutral via the already-built CFBD client (#3, #5).
4. Recency weighting + carryover for the points ratings (#4).
5. Pace layer and weather-on-totals (#6, #8), then distribution realism (#9, #10).
</content>
</invoke>
