# MLB Run-Projection Gap Analysis — project547 vs. best-in-class

**Mission:** find what leading MLB projection systems do that we don't, focused
only on accurately projecting **Team A runs / Team B runs / game total**. Betting
logic is downstream and out of scope.

**Bottom line:** our game model is a *team-rate* model — a single shrunk
runs/game number per side, nudged by the opposing staff's aggregate FIP, park,
and temperature, then drawn from a negative binomial. It is clean and
well-calibrated for what it is, but it throws away almost every player-level
input the repo already ingests. The best systems (THE BAT X, ZiPS/FanGraphs game
odds, PECOTA) build team runs **from the nine individual batters vs. the specific
starter and bullpen, platoon-adjusted, through linear weights / BaseRuns.** That
lineup-level construction is the single largest accuracy lever available to us,
and we already hold the data to do most of it.

---

## STEP 1 — How our model builds expected runs today (grounded in code)

Everything flows through `models/game.py::expected_runs()` and its call site
`pipeline.py::project_games()`.

### Inputs per side (`game.py:19-30`, assembled at `pipeline.py:297-316`)
- `runs_per_game` — recent scoring rate, **pre-shrunk** in
  `pipeline.py::_team_runs_per_game` (`pipeline.py:120-135`): last
  `TEAM_FORM_GAMES=30` games (`config.py:79`), weight `RATING_SHRINK·min(1,n/10)`
  toward `LEAGUE_RUNS_PER_GAME=4.5` (`generic.py:54`, `config.py:73`).
- `opp_starter_xfip` — the opposing starter's xFIP/FIP/ERA, resolved by name from
  our own box-log table (`pipeline.py:269-275`, `internal_stats.pitcher_table`).
  FanGraphs `xFIP` only if the pybaseball fallback fires (`statcast.py:33`).
- `opp_bullpen_xfip` — team relief-corps FIP, shrunk (`internal_stats.bullpen_fip`,
  `internal_stats.py:169`).
- `park_factor` / `own_home_pf` — a **single scalar** per venue
  (`parks.py:71`, `park_factors.json`: 30 numbers, no handedness or batted-ball
  split).
- `temp_f` — first-pitch temperature from Open-Meteo (`weather.py:37`).
- `ump_runs_factor` — home-plate ump run index (`umpires.py:57`).

### The math (`game.py:42-86`)
1. **Base:** `w·runs_per_game + (1-w)·league`, `TEAM_RATE_WEIGHT=0.65`
   (`game.py:43-45`). This is a *second* regression on top of the one already
   done in `_team_runs_per_game`.
2. **Opposing pitching:** fixed innings split `STARTER_INNINGS_SHARE=5.3/9`
   (`config.py:85`). `base ·= share·sp_factor + (1-share)·bp_factor`, where each
   factor is `clip(opp_fip/league_fip, 0.6, 1.5)` (`game.py:51-58`). Purely
   aggregate — the starter is one number, the pen is one number.
3. **Park:** de-bias the team's own home PF, then apply the venue PF,
   `PARK_WEIGHT=1.0` (`game.py:63-66`).
4. **Temperature:** game-level multiplier, `TEMP_COEF=0.003`/°F off a 72°F
   baseline, clamped ±8% (`game.py:71-74`, `config.py:103-105`). **Wind is fetched
   but never used** (`weather.py:72-77` returns `wind_mph`/`wind_dir`; no reader in
   `game.py`).
5. **Umpire runs:** multiplier, but `UMPIRE_RUNS_WEIGHT=0.0` — **off** by design
   (`config.py:132`; K tendency is on for props only).
6. **HFA:** ±`HOME_FIELD_RUNS/2 = ±0.06` runs (`game.py:82-85`).

### Distribution (`game.py:96-155`)
- Each side drawn independently from a **negative binomial** (gamma-Poisson,
  `RUN_DISPERSION=2.3`, empirically matched to var/mean≈2.33; `config.py:154`).
  This part is genuinely best-in-class — plain Poisson would be overconfident in
  the tails, and the dispersion value is validated, not curve-fit.
- Ties broken by single-inning Poisson increments (`game.py:131-135`).
- 20k draws → win prob, total mean, P(over) grid, run-line cover.

### The NRFI model is more sophisticated than the full-game model
`models/nrfi.py` already does log5 offense-vs-defense, refines the **specific
starter** (`starter_allow_rate`, `nrfi.py:91`) and the **confirmed top-3 hitters'
wOBA** (`top3_off_rate`, `nrfi.py:103`), and reads live lineups
(`pipeline.py:319-333`). The full-game model uses none of this player-level
signal — a striking asymmetry.

### Data feeds available in-repo
| Feed | Location | Used for game runs? |
|---|---|---|
| StatsAPI schedule / recent results / lineups / officials / workload / handedness / live splits | `clients/mlb_statsapi`, `platoon.py`, `umpires.py` | Lineups **not** used for runs (NRFI+props only) |
| Own box-log rates: FIP, K%, H/9, BB/9, bullpen FIP, batter AVG/SLG/ISO/K%/BB% | `internal_stats.py:135,169,190` | Starter+pen FIP only |
| Statcast expected stats **xwOBA / xBA / xSLG per player** | `data/history/backfill/mlb/<yr>/statcast_xstats.json.gz`, `history.statcast_xstats` | Batter xBA/xSLG surfaced in `batter_table` but **not** in game runs |
| Statcast `pitcher_arsenals.json`, `team_whiff.json` (CSW%, chase%, whiff by pitch) | `data/history/statcast/` | **Dead — read nowhere** |
| Platoon splits vL/vR + handedness | `platoon.py`, `splits.json.gz`, `mlb_handedness.json` | **Props only** |
| FantasyPros **daily per-player projections** | `pipeline.py:181`, `_fp_projections` | **Props only — not blended into game runs** |
| BettingPros game odds / projections / team totals | `history.bp_game_odds`, `baseline.py` | Baseline/EV only |
| Park factors (single scalar) | `park_factors.json` | Yes |
| Weather temp **+ wind** | `weather.py` | Temp only |
| Bullpen fatigue (2-day relief IP) | `internal_stats.bullpen_fatigue:114` | Card context only — **not applied to runs** |

---

## STEP 2 — What best-in-class systems do

**THE BAT / THE BAT X (Derek Carty).** The reference standard for *game-level run*
accuracy because it is explicitly a game-simulation system, not just a
season-projection. It projects each of the nine batters' **plate-appearance
outcome distribution** (K/BB/HBP/1B/2B/3B/HR/out) against the specific
pitcher, then converts to runs via a Markov/linear-weights base-out engine.
THE BAT **X** adds Statcast: batted-ball quality (xwOBA, launch/EV) regresses the
outcome rates faster and better than results-based stats. Drivers of its edge:
per-batter platoon (handedness), park factors that are **component- and
handedness-specific** (a LH-pull park inflates LHB HR only), weather/wind vectors,
umpire zone, times-through-order penalty on the starter, and explicit bullpen
modeling.

**ZiPS (Szymborski) + FanGraphs game odds.** ZiPS projects each player's rate
line (heavy multi-year regression, aging curves, comparable-player
"zSCORE" similarity). FanGraphs game odds feed the day's projected lineups
(platoon-adjusted) and starters into a BaseRuns/linear-weights run estimator plus
a bullpen depth chart, park, and a Monte Carlo. The accuracy comes from (a)
**lineup dependency** — the actual 9 hitters posted, not the team average — and
(b) heavy **regression/aging** so April samples don't dominate.

**Steamer.** Similar rate-projection philosophy; regresses component skills
(each with its own reliability/regression constant tuned by out-of-sample
back-testing) and weights recent seasons. Feeds the same lineup→linear-weights
run pipeline.

**PECOTA (Baseball Prospectus).** Comparable-player (nearest-neighbor) projections
with aging, plus **DRC+** (deserved runs, park/quality-of-competition adjusted) and
strong **defense (FRAA) and catcher-framing** run-prevention inputs. Its team-run
distributions come from simulating the roster.

**The sharp market.** The de-vigged closing total/team-total is the single most
accurate public estimate of expected runs — it aggregates lineups, weather, and
sharp models. Our own research (`nrfi.py:20-26`, `config.py:216-225`) already
concedes MLB markets are efficient; the market is the calibration anchor, not a
projection *mechanism* we can copy, but team-total closing lines are the best
external validator for our run projections.

**Common mechanics that drive their accuracy, in rough order:**
1. **Lineup-level batter-vs-pitcher run construction** (platoon-adjusted wOBA →
   linear weights / BaseRuns). This is the core; everything else is a modifier.
2. **Statcast quality-of-contact (xwOBA)** to regress rates faster than results.
3. **Regression + aging + multi-source blending** of the underlying rates.
4. **Component/handedness/park factors** (multi-dimensional, not one scalar).
5. **Times-through-order penalty** and explicit **starter→bullpen transition**.
6. **Weather vectors** (wind speed *and* direction relative to park orientation,
   altitude, temperature).
7. **Defense (OAA/DRS) and catcher framing** run-prevention.
8. **Over-dispersed team-run distribution** (negative binomial) — the one thing
   we already do well.

---

## STEP 3 — Gap analysis (ranked by accuracy impact ÷ effort)

Data-readiness tags: **[HAVE]** in-repo now · **[DERIVE]** computable from feeds
we already pull · **[NEW DATA]** needs a new feed.

### Rank 1 — Lineup-level batter-vs-pitcher run construction  · Impact: **HIGH** · [HAVE]
- **Technique:** build each side's expected runs from the **9 posted batters**
  vs. the specific starter (then bullpen), each batter's wOBA/xwOBA
  platoon-adjusted for the pitcher's hand, aggregated through linear weights or
  BaseRuns instead of a single team runs/game number.
- **Why HIGH:** this is *the* mechanism separating THE BAT X/ZiPS game odds from a
  team-rate model. A lineup missing its 3-hole bat, or stacked L vs a LHP, moves a
  team total by 0.5–1.0 runs — invisible to our current model. It also fixes the
  home/away *margin*, not just the total.
- **Our gap:** `game.py::expected_runs` never sees a lineup. Lineups are pulled
  (`pipeline.py:319`) and already feed NRFI's top-3 (`nrfi.py:103`) and props, but
  the full-game run number is pure team rate (`game.py:43-58`).
- **Sketch:** we already have every ingredient — `mlb_statsapi.batting_order`
  (`pipeline.py:319`), per-batter wOBA proxy `_batter_woba_map` (`pipeline.py:211`),
  Statcast `xwoba` (`statcast_xstats.json.gz`), platoon multipliers
  (`platoon.platoon_mult`, `platoon.py:213`). Add a
  `models/game.py::lineup_expected_runs(lineup, starter, bullpen, park)` that (1)
  maps each batter to a platoon-adjusted wOBA, (2) converts lineup wOBA→runs via a
  linear-weights/BaseRuns constant anchored to `LEAGUE_RUNS_PER_GAME`, (3) blends
  with the existing team-rate `base` (e.g. 0.5/0.5, tuned on the walk-forward
  totals MAE like `TEMP_COEF`). Fall back to today's team-rate path when the
  lineup isn't posted. This is the highest-value change and needs **no new feed.**

### Rank 2 — Wire Statcast xwOBA into the rate inputs  · Impact: **Med-High** · [HAVE]
- **Technique:** regress batter and pitcher rates toward **xwOBA / xERA**
  (quality-of-contact) rather than raw AVG/SLG/FIP, especially early season.
- **Why:** xwOBA stabilizes far faster than results; it is most of THE BAT X's
  edge over THE BAT. Cheap once Rank 1 exists because the run engine already
  consumes wOBA.
- **Our gap:** `internal_stats.batter_table` loads prior-season `est_ba/est_slg`
  (`internal_stats.py:211-219`) but the game model never reads them; the current
  season's `statcast_xstats.json.gz` (which includes `xwoba`) is unused for
  batters, and `pitcher_arsenals.json`/`team_whiff.json` are read nowhere.
- **Sketch:** in `_batter_woba_map` (`pipeline.py:211`) prefer `xwoba` from
  `history.statcast_xstats(season)` over the AVG/SLG proxy; shrink the current-year
  xwOBA by PA toward prior-year xwOBA. For pitchers, blend xFIP with xwOBA-against
  in `starter_xfip` (`pipeline.py:269`).

### Rank 3 — Use the wind vector we already fetch  · Impact: **Med** · [HAVE]
- **Technique:** wind blowing out ↑ run environment, in ↓; effect scales with
  speed and direction relative to the park's orientation. A 15 mph out-wind at
  Wrigley is worth ~0.5–1.0 total runs.
- **Why Med:** real and well-documented, but only meaningful at a handful of
  open, wind-exposed parks and only on windy days — smaller and rarer than
  temperature.
- **Our gap:** `weather.game_weather` returns `wind_mph`+`wind_dir`
  (`weather.py:72-77`); `expected_runs` reads only `temp_f` (`game.py:71`). Pure
  waste of an ingested signal.
- **Sketch:** add a per-park center-field bearing table (small constant, like
  `weather.PARKS`), compute the out/in component, and add a clamped game-level
  multiplier next to the temperature block (`game.py:71-74`). Gate it behind a
  `WIND_COEF` config knob tuned on the walk-forward totals MAE, exactly like
  `TEMP_COEF`. Skip domes (`sky=="dome"` already in `game_context`).

### Rank 4 — Times-through-order + real starter→bullpen transition on runs  · Impact: **Med** · [DERIVE]
- **Technique:** a starter allows progressively more per TTO (~+10% wOBA-against
  by the 3rd time); model the innings the *specific* starter is likely to cover
  (from workload) and hand the rest to the pen, instead of a fixed 5.3/9 with a
  flat multiplier.
- **Why Med:** the fixed split already captures the average; the win is on
  outliers (a short-leash opener, a workhorse, a gassed pen).
- **Our gap:** `STARTER_INNINGS_SHARE` is a constant (`config.py:85`, applied
  `game.py:57-58`); the TTO penalty exists **only for K props**
  (`props.py:91-99`) and the per-start expected innings from the pitch-count feed
  (`props.py`, `pipeline.py:419`) is computed but not used for runs.
- **Sketch:** replace the constant `share` in `expected_runs` with a per-game
  value derived from `refine_expected_innings` (already computed in props), and
  apply a TTO run-inflation to the starter share mirroring
  `props.times_through_order_k_factor`.

### Rank 5 — Bullpen fatigue / availability into runs  · Impact: **Med** · [HAVE]
- **Technique:** a pen that threw 4.5+ IP over the prior two days is worse
  tonight; lower its effective quality (or shift innings toward mop-up arms).
- **Why Med:** real on ~15–20% of slates; second-order the rest.
- **Our gap:** `bullpen_fatigue` is fully computed (`internal_stats.py:114`) and
  surfaced on the matchup card (`teamstats.py:544-552`) but **never touches
  `expected_runs`**.
- **Sketch:** in `pipeline.py:301/311` scale `opp_bullpen_xfip` upward when the
  opposing pen's `level=="heavy"`, or pass a fatigue multiplier into a new
  `TeamInputs` field consumed in the pitching block (`game.py:55-56`).

### Rank 6 — Blend FantasyPros daily projections into game runs  · Impact: **Med** · [HAVE]
- **Technique:** FP daily projections are lineup- and matchup-aware external
  numbers; summing a team's projected runs (or hits/HR→runs) gives a second,
  independent run estimate to blend — the "multi-source" trick ZiPS/Steamer/THE
  BAT get from consensus.
- **Why Med:** ensembling with a decent external projection reliably cuts MAE;
  `FP_BLEND_WEIGHT=0.5` already exists (`config.py:157`) but only props use it.
- **Our gap:** `_fp_projections` is called in `project_props` only
  (`pipeline.py:378`); `project_games` never pulls FP.
- **Sketch:** pull `_fp_projections` in `project_games`, aggregate each side's
  batter projections into a team-run estimate, and blend with the model
  `total_mean` at `FP_BLEND_WEIGHT`.

### Rank 7 — Multi-dimensional / handedness-split park factors  · Impact: **Med** · [NEW DATA] / [DERIVE]
- **Technique:** replace the one scalar with component (1B/2B/3B/HR) and
  LHB/RHB-split factors, so a lefty-stacked lineup in a LH-friendly park gets the
  right bump.
- **Why Med (not High):** interacts with Rank 1 — only pays off *once* runs are
  built from handed batters. Alone it barely moves a team-rate model.
- **Our gap:** `park_factors.json` is 30 scalars (`parks.py`), method
  "home/road runs-per-game, shrunk."
- **Sketch:** **[NEW DATA]** ingest Statcast park factors by handedness/batted-ball
  from Baseball Savant, *or* **[DERIVE]** approximate HR-park splits from our
  Retrosheet `game_context` + box logs. Consume in the Rank-1 run engine, not the
  scalar `game.py:63-66` path.

### Rank 8 — Defense (OAA/DRS) + catcher framing run-prevention  · Impact: **Low-Med** · [NEW DATA]
- **Technique:** credit run prevention to fielding (OAA/DRS) and pitch-framing
  (catcher framing runs), which xFIP/FIP miss entirely.
- **Why Low-Med:** real but small (a great defense is ~0.2–0.3 runs/game) and
  noisy; PECOTA leans on it but the marginal accuracy per unit effort is lower
  than Ranks 1–6, and it needs a feed we don't have.
- **Our gap:** no defensive or framing data in-repo (`grep` finds only NFL EPA
  "defense").
- **Sketch:** **[NEW DATA]** pull season OAA/DRS + catcher framing from Baseball
  Savant/FanGraphs; convert to a small per-team run-prevention multiplier on the
  opponent's `base` in `expected_runs`.

### Rank 9 — Regression / aging / projection blending of the base rate  · Impact: **Low-Med** · [DERIVE]
- **Technique:** proper multi-year, aging-adjusted rate projections (ZiPS/Steamer
  style) instead of last-30-games recency.
- **Why Low-Med:** our double shrinkage (`_team_runs_per_game` +
  `TEAM_RATE_WEIGHT`) already tames streaks; full aging curves are a large build
  for a modest team-total gain, and much of the benefit is captured once Rank 1 +
  Rank 2 (xwOBA regression) land.
- **Our gap:** recency-only, no aging (`pipeline.py:120-135`, `game.py:43-45`).
- **Sketch:** blend prior-season team/park-adjusted runs as an explicit prior in
  `_team_runs_per_game`; longer-term, use `statcast_xstats` multi-year per-player.

### Already best-in-class (keep)
- **Over-dispersed run distribution** — negative binomial at `RUN_DISPERSION=2.3`
  (`game.py:96-108`, `config.py:154`), validated and matching measured var/mean.
  Do **not** revert to Poisson.
- **Market shrink as a bet-selection guardrail** (`config.py:216-225`) — correct
  posture given efficient MLB markets, though it is downstream of projection.

---

## Recommended sequencing
Ranks 1 → 2 → 6 are the highest impact-per-effort and are **all [HAVE]** — no new
feeds, they reuse lineups, xwOBA, and FantasyPros data already flowing through the
pipeline. Ranks 3, 4, 5 are small, self-contained wins on data already fetched.
Ranks 7–9 depend on new feeds or only pay off after the lineup engine exists.
Every change should be tuned and gated exactly like `TEMP_COEF`/`RUN_DISPERSION`
were — a config knob validated on the walk-forward totals MAE and team-total
closing-line calibration, defaulting inert until it proves out.
