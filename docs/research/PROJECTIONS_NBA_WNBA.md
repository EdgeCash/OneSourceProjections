# NBA & WNBA Score/Total Projection — State of the Art vs. project547

Research date: 2026-07-07
Scope: accuracy of **Team A points / Team B points / game total** for NBA and WNBA.
Wagering/EV logic is explicitly out of scope (downstream). Every claim about our
code is grounded in a file:line that was read.

---

## 0. TL;DR

Our NBA/WNBA game model projects each side's points from **raw recent points-per-game**
(shrunk, log5-combined) and sets the **total = home_exp + away_exp**. It never uses
**possessions/pace or offensive/defensive efficiency**, even though we already derive
pace, off_rtg, eFG%, TS% per team-game in `teamstats.py` — those live only in the
research *card*, not the projection. That is the single biggest gap: best-in-class
basketball systems are pace-and-efficiency engines (points per 100 possessions ×
projected possessions), which is exactly the decomposition that makes totals and
extreme-tempo matchups accurate.

Ranked top levers (impact ÷ effort): **(1) pace-and-efficiency scoring**,
**(2) recency-weighted team ratings**, **(3) reconcile the total/side-scores with the
Elo/rest blend**, **(4) opponent-adjusted efficiency (iterative SoS)**,
**(5) injuries & minutes redistribution** (highest raw impact, but needs a new feed).

---

## 1. What our model does today (exact mechanics)

### 1.1 Team ratings — recent points, shrunk toward league (no pace)
`generic.team_ratings()` (`project547/models/generic.py:36`) builds, per team, a
**points-scored and points-allowed per game** from the game *results* list
(`{home_team, away_team, home_score, away_score}` only — see the ESPN feed at
`clients/espn.py:393` `results_range`, which carries **final scores only**, no box
detail):

- Shrinkage toward league PPG with `RATING_SHRINK = 0.65` (`generic.py:26`), ramped by
  sample: `w = 0.65 * min(1.0, n/10)` (`generic.py:54`). So a team with ≥10 games sits
  at 65% observed / 35% league mean; thinner samples pull harder to the mean.
- **Equal weight to every game** in the window — there is no recency weighting inside
  `team_ratings` (contrast the player-prop models, which use an explicit exponential
  half-life, `wnba_props.py:70` `weighted_rate`). The only recency control is the
  hard cutoff `form_days` (NBA/WNBA = 45, `sports.py:108`/`:91`).
- Optional one-pass strength-of-schedule: `opponent_adjust` adds
  `league − mean(opponent allowed)` to offense and the mirror to defense
  (`generic.py:60-64`). It is a **single additive pass on raw PPG**, **ON for NBA**
  (`sports.py:117`) and **OFF for WNBA** (`sports.py:104`, "Elo already carries SoS").

### 1.2 Expected score — log5-for-points on PPG, flat HFA
`expected_score()` (`generic.py:69`) with `score_method="multiplicative"` for both
NBA and WNBA (`sports.py:99`, `:109`):

```
h_exp = league × (h_off/league) × (a_def/league) + hfa/2     # generic.py:83
a_exp = league × (a_off/league) × (h_def/league) − hfa/2     # generic.py:84
```

- `league` = `league_ppg`: **NBA 114.0** (`sports.py:107`), **WNBA 82.0** (`sports.py:91`).
- `hfa` = **flat 2.5 points** for both, split ±1.25 per side (`sports.py:91`, `:107`).
  **No venue/altitude/travel differentiation** — every home court is worth the same.
- `h_off`, `a_def` etc. are the **shrunk PPG** from §1.1 — **possessions/pace never
  enter**. A fast team and a slow team with the same PPG are treated identically.

### 1.3 Total and distribution
- **Total mean = home_exp + away_exp** (`generic.py:147`, surfaced at `pipeline.py:978`).
  The total is a pure by-product of the two side scores; it is **not** separately
  modeled from projected possessions, and it is **never** touched by Elo or rest
  (by design — see §1.4).
- `P(over)` = Normal CDF with a **single constant** `sigma_total`
  (`generic.py:106`): **NBA 19.0** (`sports.py:107`), **WNBA 17.0** (`sports.py:90`).
  These are empirical walk-forward residual SDs (comment `sports.py:88-90`). Constant
  across all games — it does **not** scale with the projected total/pace.

### 1.4 Margin, Elo, rest — and the side-score inconsistency
In `project_generic_games()` (`pipeline.py:906`):
1. `project_game` produces `home_win_prob` from the points margin through
   `sigma_margin` (`generic.py:140`).
2. **Elo blend** on the *win probability* only (`pipeline.py:957-960`):
   `hwp = (1−blend)·model + blend·elo`. `elo_blend` = **NBA 0.60** (`sports.py:113`),
   **WNBA 0.65** (`sports.py:93`). Elo config is the 538-style logistic with a
   log-MOV multiplier + autocorrelation correction (`models/elo.py:50-67`); defaults
   tuned for WNBA (`elo.py:17-24`).
3. **Rest** as a win-prob nudge (`pipeline.py:961-964`) via `shift_win_prob`
   (`generic.py:120`): `delta = rest_coeff × (home_rest − away_rest)`, each rest
   capped at 14 days (`pipeline.py:949`). `rest_coeff` = **NBA 0.5** (`sports.py:116`),
   **WNBA 0.0 — OFF** (`sports.py:101`, measured to hurt Brier in the current setup).
4. `with_consistent_margin` (`generic.py:173`, called `pipeline.py:968`) back-solves a
   margin mean from the *adjusted win prob* through `sigma_margin` so the spread agrees
   with the moneyline.

**Consequence worth flagging:** step 4 rewrites `margin_mean` but **leaves `home_exp`,
`away_exp`, and `total_mean` untouched** (`generic.py:196-200`). So after an Elo/rest
blend, the **published side scores and total no longer reconcile with the published
margin** (`home_exp − away_exp ≠ margin_mean`), and **the total absorbs none of the
Elo/rest information**. Totals are intentionally left to the points model, but the side
scores are then internally inconsistent with the win/margin we show.

### 1.5 What data we already have (feeds)
- **Final scores** for every game (`espn.results_range`, `clients/espn.py:393`) — the
  only feed the game model consumes today.
- **Full team box aggregates** derived in `teamstats.py:_basketball_team_games`
  (`teamstats.py:216`): per team-game **possessions** (`poss = FGA − OREB + TOV +
  0.44·FTA`, `teamstats.py:241`), **pace**, **off_rtg = pts/poss×100**
  (`teamstats.py:243`), **eFG%**, **TS%**, **OREB%**, plus the **opponent's** off_rtg/
  eFG joined as "allowed" (`teamstats.py:250-253`). **This is computed but used only
  for the research card** (`matchup()`, `teamstats.py:495`) — it does **not** feed
  `project_generic_games`.
- **Player box logs** with minutes/points/etc. (`history.player_games`, used by
  `nba_props.py`/`wnba_props.py`) — the raw material for minutes-based projection, but
  there is **no forward injury/lineup feed** to know who will play.
- **No** venue coordinates, travel, or injury/inactive feed anywhere in the game path
  (grep for `injur|venue|altitude|travel|b2b` returns only MLB park/starter code).

---

## 2. Best-in-class basketball projection systems (benchmark)

### 2.1 The universal backbone: pace-and-efficiency ("tempo-free")
Every serious basketball model (Pomeroy/KenPom, Haslametrics, ESPN BPI, Inpredictable,
Torvik, and the pro sharps) decomposes scoring into **tempo × efficiency**:

- **Possessions (pace):** estimate each team's tempo. Game possessions ≈ a blend of the
  two teams' pace relative to league (a fast team + slow team meet in the middle). The
  standard box estimate is `Poss = FGA − OREB + TOV + 0.44·FTA` (Dean Oliver) — **the
  same formula we already compute at `teamstats.py:241`**.
- **Efficiency:** **Offensive Rating (ORtg) = points per 100 possessions**, and
  **Defensive Rating (DRtg) = points allowed per 100**. Ratings are the unit of team
  strength, *independent of tempo*.
- **Projection:** projected possessions `P̂` from the two paces; each side's points =
  `P̂ × (adjusted ORtg vs opponent's adjusted DRtg) / 100`; **total = sum**. This is
  why these systems nail totals: a high-total game is high because of **either** more
  possessions **or** higher efficiency, and the model knows which.

Why this beats raw-PPG log5 (our approach): PPG conflates pace and efficiency. Two teams
at 112 PPG — one at 105 possessions/low efficiency, one at 95 possessions/high
efficiency — produce very different totals against a given opponent, but our
`expected_score` treats them the same. Pace-and-efficiency is the largest single
accuracy lever in basketball projection.

### 2.2 Ken Pomeroy-style adjusted efficiency (opponent adjustment done right)
KenPom's core: **adjusted** ORtg/DRtg = a team's raw efficiency re-expressed as "what it
would be vs. an average opponent on a neutral floor." This is an **iterative**
opponent adjustment (each team's rating depends on its opponents' ratings, solved to
convergence, i.e. a ridge/least-squares or fixed-point solve) — **not** a single
additive pass. It also uses **recency/preseason priors** (early-season ratings are
pulled toward a returning-talent prior). The adjusted-efficiency margin is the best
single predictor of point spread in college basketball and is the template BPI/Haslam
follow for the pros.

### 2.3 ESPN BPI, FiveThirtyEight RAPTOR/CARMELO, Inpredictable
- **ESPN BPI:** a point-per-100 power rating built bottom-up, explicitly adjusting for
  **pace, opponent strength, rest/travel, and player availability (minutes-weighted
  player ratings)** — availability is a first-class input, not an afterthought.
- **538 RAPTOR (player) → team rating:** team strength = sum of players' RAPTOR
  (offense/defense per 100) weighted by **projected minutes**. **CARMELO/CARM-Elo**
  wraps a MOV-aware Elo with player-based priors. The key idea for us: the team rating
  is **rebuilt from the minutes you expect to actually play**, so a star resting drops
  the rating by that star's per-100 value × lost minutes, and his minutes redistribute
  to bench players at their (worse) efficiency and higher usage.
- **Inpredictable / sharp market:** pace-and-efficiency priors continuously updated,
  then blended toward the **market total/spread** (the closing line is the strongest
  single estimator). The market is used as a prior/benchmark; projection systems that
  beat it do so mainly through **faster, better injury/minutes handling** and clean
  pace estimation.

### 2.4 Dean Oliver's Four Factors (the efficiency drivers)
Efficiency itself decomposes into, in priority order: **eFG% (shooting, ~40%),
turnover rate (~25%), offensive-rebound rate (~20%), free-throw rate (~15%)**. Modeling
ORtg/DRtg through the Four Factors (which we already compute — eFG% `teamstats.py:244`,
TOV, OREB% `teamstats.py:258`, FT rate available) gives more stable, faster-converging
ratings than raw points, because shooting and rebounding stabilize quicker than points.

### 2.5 Situational adjustments the pros apply
- **Rest / back-to-backs / schedule density:** not just days-rest but **B2B, 3-in-4,
  4-in-6, 5-in-7**. A B2B is worth roughly **−2 to −3 points** to the tired team and
  **lowers the total** (tired legs → worse shooting/pace). Crucially the effect belongs
  on **both the margin and the total**, not margin only.
- **Home court by venue:** base HFA ~**2.5–3.0** NBA, but **altitude venues (Denver,
  Utah)** carry a measurable extra edge, and travel distance/time-zone changes matter.
- **Injuries & minutes redistribution:** the dominant source of single-game error.
  Star out → (a) subtract his per-100 value over his minutes, (b) redistribute those
  minutes to replacements at their efficiency, (c) **usage shifts** to remaining
  starters (their efficiency dips slightly at higher usage). Handled via minutes-weighted
  player ratings (RAPTOR/BPI style).

### 2.6 Distribution / SD (for probabilities)
- **Margin SD** in the NBA is famously stable at **≈11–13 points** regardless of the
  teams — our 12.5 (`sports.py:107`) / 12.7 WNBA (`sports.py:91`) are right in band.
- **Total SD** is **not** constant: it **scales with the expected total/pace** (more
  possessions → more variance). Sharp systems use a total SD that grows with the
  projected total (roughly √-scaling with possessions) rather than a single constant.
  Our constant `sigma_total` (19 NBA / 17 WNBA) is a reasonable average but mis-sizes
  the tails for very high- or low-total games.

### 2.7 WNBA-specific differences
- **Tiny samples:** ~40 games/season (vs 82 NBA). Ratings need **heavier shrinkage,
  stronger cross-season carry, and recency weighting** to be stable — a single 30-point
  game moves a WNBA season average far more than an NBA one.
- **Roster volatility:** Olympic/EuroBasket breaks, overseas commitments, and heavy
  load management make **availability an even larger share of variance** than in the
  NBA. A minutes/injury feed is higher-value here than anywhere.
- **Pace/efficiency levels differ:** lower pace and lower league efficiency; opponent
  adjustment matters because schedule imbalance is large in a short season. We currently
  have `opponent_adjust` **OFF** for WNBA (`sports.py:104`).
- Our Elo is already WNBA-tuned (`elo.py:17`, `elo_regress=0.5` at `sports.py:98`), which
  is good; the gap is on the **points/efficiency** side, not the win-prob side.

---

## 3. Gap analysis (ranked by impact ÷ effort)

Legend — data readiness: **[HAVE]** in the model path · **[DERIVE]** computable from data
we already have (usually already computed for the card) · **[NEW DATA]** needs a feed we
don't ingest.

### Rank 1 — Pace-and-efficiency scoring engine  ·  Impact: **HIGH**  ·  Effort: **LOW**  ·  **[DERIVE]**
- **Technique:** project each side's points as `possessions × (adj ORtg vs adj DRtg)/100`
  and the **total from projected possessions**, instead of log5 on raw PPG.
- **Why HIGH:** biggest single accuracy lever in basketball; fixes totals in
  extreme-pace matchups and separates "scores a lot because fast" from "because
  efficient." Directly targets Team A / Team B / total accuracy — the mission metric.
- **Our gap:** `expected_score` (`generic.py:69-88`) uses shrunk **PPG** and never sees
  possessions; the total is just the PPG sum (`generic.py:147`). Pace/ORtg/DRtg exist
  but only in the card (`teamstats.py:241-243`).
- **Data readiness:** **[DERIVE]** — possessions, off_rtg, and opponent-allowed off_rtg
  are already computed at `teamstats.py:241-253` from `history.player_games`. No new
  feed.
- **Implementation sketch:**
  1. New `pace_efficiency_ratings()` in `models/generic.py` (or a small
     `models/basketball.py`) that consumes team box aggregates instead of scores:
     per team, shrunk **pace**, shrunk **adj ORtg**, shrunk **adj DRtg** (reuse the
     shrink pattern at `generic.py:52-56`).
  2. New score path: `P̂ = f(pace_home, pace_away, league_pace)`;
     `h_pts = P̂ × (h_ORtg × a_DRtg / league_ORtg)/100 + hfa/2`. Gate behind
     `sport.score_method == "pace_eff"` in `expected_score` so NFL/soccer are untouched.
  3. Feed it in `project_generic_games` (`pipeline.py:917`) for NBA/WNBA by pulling
     `teamstats.team_games` aggregates instead of / alongside `espn.results_range`.
  4. Validate walk-forward against the current PPG path (total RMSE, side-score MAE)
     before flipping the sport flag — mirrors the existing `epa_blend`/validation gate
     discipline (`sports.py:59`, `generic.py:152`).

### Rank 2 — Recency-weighted team ratings  ·  Impact: **MED**  ·  Effort: **LOW**  ·  **[DERIVE]**
- **Technique:** exponential half-life weighting of recent games inside the team rating
  (form matters; teams change within a season).
- **Why MED:** `team_ratings` currently equal-weights every game in the 45-day window
  (`generic.py:52-56`) — a hot/cold stretch is diluted by month-old games. The prop
  models already do this well (`wnba_props.py:70` half-life); the game model doesn't.
- **Our gap:** no recency weighting in `generic.team_ratings` (`generic.py:36-66`); only
  a hard `form_days` cutoff.
- **Data readiness:** **[DERIVE]** — same game list, add a weight.
- **Implementation sketch:** add an exponential weight `0.5**(age_games/half_life)` to
  the mean at `generic.py:55` (and to the pace/efficiency version from Rank 1). Half-life
  ~ NBA 15–20 games / WNBA 6–8 (matching the shorter season, cf. `wnba_props.py:49`
  HALF_LIFE=8, `nba_props.py:41` HALF_LIFE=10). Tune walk-forward.

### Rank 3 — Reconcile total & side-scores with the Elo/rest blend  ·  Impact: **MED**  ·  Effort: **LOW**  ·  **[HAVE]**
- **Technique:** after the win-prob is adjusted by Elo/rest, re-derive **home_exp and
  away_exp** from (total, blended margin) so the three published numbers agree, and let
  the **total** absorb the rest signal (a B2B lowers the total, not just the margin).
- **Why MED:** today `with_consistent_margin` fixes the spread but leaves
  `home_exp`/`away_exp`/`total_mean` reflecting only the raw PPG model
  (`generic.py:196-200`), so the side scores we publish contradict our own margin, and
  the total ignores rest entirely. Cheap correctness win on the exact outputs the
  mission cares about.
- **Our gap:** `generic.py:173-200` and `pipeline.py:965-969`.
- **Data readiness:** **[HAVE]**.
- **Implementation sketch:** in `with_consistent_margin`, once `margin` is back-solved,
  set `home_exp = (total + margin)/2`, `away_exp = (total − margin)/2`. Separately, add
  a `total` rest/pace adjustment (a small negative points delta on the total for the
  tired side's B2B) rather than routing rest only through `shift_win_prob`
  (`pipeline.py:961-964`).

### Rank 4 — Iterative opponent-adjusted efficiency (proper SoS)  ·  Impact: **MED–HIGH**  ·  Effort: **MED**  ·  **[DERIVE]**
- **Technique:** KenPom-style adjusted ORtg/DRtg solved to convergence, replacing the
  single additive pass.
- **Why MED-HIGH:** our SoS is one additive pass on **raw PPG** (`generic.py:60-64`),
  and it's **OFF for WNBA** where schedule imbalance in a 40-game season is largest.
  Adjusting **efficiency** (which stabilizes faster than points) and iterating removes
  most early-season schedule bias.
- **Our gap:** `generic.py:58-65` (one pass, additive, PPG); `opponent_adjust=False`
  for WNBA (`sports.py:104`).
- **Data readiness:** **[DERIVE]** — opponent off_rtg/def already joined at
  `teamstats.py:250-253`.
- **Implementation sketch:** replace the one-pass block with a fixed-point loop
  (3–5 iterations, or a ridge solve) on ORtg/DRtg; couple to the Rank-1 engine. Re-test
  `opponent_adjust` for WNBA once it operates on efficiency, not PPG (the prior
  "neutral/hurts" finding at `sports.py:102-104` was measured on the raw-PPG pass).

### Rank 5 — Injuries & minutes redistribution  ·  Impact: **HIGH**  ·  Effort: **HIGH**  ·  **[NEW DATA]**
- **Technique:** rebuild team ratings from **projected minutes** of available players
  (RAPTOR/BPI style): star out → subtract his per-100 value over his minutes,
  redistribute to bench, shift usage.
- **Why HIGH (but expensive):** the dominant driver of single-game error; a top scorer
  ruled out can move a team total by 6–10 points. We currently do **nothing** — the game
  model never sees a lineup.
- **Our gap:** no injury/lineup feed in the game path (grep confirms only MLB has
  starter handling); `project_generic_games` (`pipeline.py:906`) is roster-blind.
- **Data readiness:** **[NEW DATA]** — need a forward **injury/inactive + projected-
  minutes feed** (e.g. ESPN injuries endpoint, Rotowire/Rotoworld, or a starting-lineups
  feed). We already have historical player per-100 value latent in
  `history.player_games` (used by `nba_props.py`), so the *player ratings* are
  derivable; only the **who-is-playing-tonight** signal is new.
- **Implementation sketch:** (a) compute per-player per-100 off/def value from box logs;
  (b) ingest an injury/lineup feed; (c) build a team rating = minutes-weighted player
  values, replacing/adjusting the Rank-1 team ORtg/DRtg when a starter is out; (d) apply
  a usage-redistribution term. Ship behind the same demonstrated-edge/validation gate.

### Rank 6 — Venue-specific home-court advantage (+ altitude/travel)  ·  Impact: **LOW–MED**  ·  Effort: **LOW–MED**  ·  **[DERIVE/NEW DATA-lite]**
- **Technique:** per-venue HFA (Denver/Utah altitude bump) and a travel/time-zone term,
  instead of flat 2.5.
- **Why LOW-MED:** real but small vs. pace/efficiency; mostly affects margin, some total
  at altitude.
- **Our gap:** flat `hfa=2.5` for every game (`sports.py:91`, `:107`).
- **Data readiness:** venue is on the ESPN event (`clients/espn.py` competition), so a
  static **venue→HFA table** is **[DERIVE]**; travel distance needs a venue-coordinates
  table (**[NEW DATA-lite]**, static).
- **Implementation sketch:** a per-team home-HFA override consumed in `expected_score`
  (`generic.py:83-84`); estimate each venue's HFA from historical home margin in
  `teamstats`/results.

### Rank 7 — Pace-scaled total SD  ·  Impact: **LOW–MED**  ·  Effort: **LOW**  ·  **[DERIVE]**
- **Technique:** make `sigma_total` grow with the projected total/possessions rather than
  a single constant.
- **Why LOW-MED:** improves tail calibration on extreme totals; the mean is unchanged.
- **Our gap:** constant `sigma_total` in `prob_over` (`generic.py:106`; `sports.py:90`,
  `:107`).
- **Data readiness:** **[DERIVE]** (needs the Rank-1 possession estimate to scale on).
- **Implementation sketch:** `sigma_total(total) = base × √(total/league_total)` (or a
  fitted linear form) inside `prob_over`; validate the residual SD by total-decile.

---

## 4. WNBA-specific callouts (separated as requested)
- **Enable opponent adjustment on efficiency** (Rank 4). It's off today (`sports.py:104`)
  because the raw-PPG one-pass version was neutral; the short 40-game schedule makes SoS
  more important, and adjusting *efficiency* is a different (better-behaved) test.
- **Shorter recency half-life** (Rank 2): ~6–8 games, matching `wnba_props.py:49`.
- **Availability is the biggest variance source** (Rank 5) — a minutes/injury feed pays
  off more in the WNBA than the NBA given roster churn and international breaks.
- **Keep the Elo win-prob machinery** — it is already WNBA-tuned (`elo.py:17`,
  `elo_regress=0.5` `sports.py:98`, `elo_blend=0.65` `sports.py:93`) and is not the gap;
  the gap is entirely on the **points/efficiency** side.
- **Re-test rest for WNBA** once rest routes through the total/efficiency engine
  (Rank 3) instead of the win-prob nudge that was measured to hurt (`sports.py:101`).

---

## 5. Grounding index (our code, verified)
- Team ratings from scores + shrinkage: `models/generic.py:26,36,52-66`
- log5-for-points score, flat HFA: `models/generic.py:69-88`; `sports.py:91,107`
- Total = sum; constant sigma_total: `models/generic.py:106,147`; `sports.py:90,107`
- Elo/rest/margin reconciliation: `pipeline.py:906,917,957-969`; `models/generic.py:120,173-200`; `models/elo.py:50-67`
- Pace/efficiency computed but card-only: `teamstats.py:216,241-253,495`
- Feeds: final-scores-only results `clients/espn.py:393`; slate `clients/espn.py:60`;
  player box logs `nba_props.py`/`wnba_props.py` via `history.player_games`
- No injury/venue in game path: grep `injur|venue|altitude|travel|b2b` → MLB-only.
