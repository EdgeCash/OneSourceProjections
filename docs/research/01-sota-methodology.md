# SOTA Methodology: NFL & NCAAF Projection / Prediction Models

Research date: 2026-06-24

This report synthesizes how the most accurate, well-regarded NFL and college football (NCAAF) projection systems actually work, with an emphasis on the methodology that drives accuracy. It is organized by theme, with concrete, actionable techniques and cited sources. Source URLs are collected at the end.

---

## 0. Executive orientation: what "accurate" means

There are two distinct accuracy goals, and they require different evaluation:

1. **Game-outcome / margin accuracy** — predicting the final scoring margin and win probability. Measured against actual results (Brier score, log loss, calibration, MAE/RMSE on margin).
2. **Market accuracy (the real bar)** — predicting *better than the betting market's closing line*. The closing line at a sharp book (Pinnacle) is the single best public estimate of true probability. A model that does not beat the closing line on average is not adding edge, no matter how good its raw accuracy looks. **"Beating the closing line" (positive CLV) is the operational definition of a winning model.** (Pinnacle/Buchdahl, VSiN, BettorEdge)

A practical consequence that almost every top system converges on: **blend your independent (non-market) power rating with the market line.** The market is a strong prior; deviate from it only where your signal is confident.

---

## 1. Modeling approaches: what actually wins for football

### 1.1 Power ratings (Elo and variants) — the backbone

Elo is the dominant, battle-tested framework for football team strength because it is online (updates after each game), interpretable, and naturally produces win probabilities and point spreads.

**FiveThirtyEight NFL Elo** (the reference implementation, now archived but foundational):
- Home-field adjustment: **+65 Elo points** to the home team (pre-COVID era).
- **K-factor = 20** (controls how fast ratings move per game).
- **Margin-of-victory (MOV) multiplier**: ratings update more for bigger wins, but with diminishing returns so blowouts (40 vs 30) barely differ. The multiplier also corrects for autocorrelation — favorites running up the score — by scaling down MOV when the favored (higher-rated) team wins big. This is the key innovation that makes Elo margin-aware without over-rewarding garbage-time points.
- Rest, bye, and playoff adjustments applied as Elo bonuses.
- Preseason: ratings **revert toward the mean** between seasons (carry over ~2/3 of prior rating) to account for roster turnover.
- 538's biggest acknowledged weakness: **no QB adjustment**, so a starter injury tanks accuracy. (FiveThirtyEight methodology; Model 284; andr3w321 on MOV)

**nfelo** (the most-cited modern NFL Elo evolution; open-source-adjacent, by @greerreNFL): takes the 538 framework and fixes its gaps:
- **Better initial/preseason ratings derived from win-total betting markets** (the market's preseason expectation is a strong prior).
- A **custom QB-adjustment model**: every team and QB gets a rolling performance rating (EPA/CPOE-based) that shifts a team's "effective" Elo up or down on injury or QB change.
- A **custom home-field-advantage model** fit on historical data (rather than a flat constant).
- **Smart market-reversion logic**: leans on the predictive power of the Vegas spread while preserving as much *non-market* signal as possible — i.e., it pulls the rating toward the market but not all the way, capturing edge where the model legitimately disagrees.
- Re-tuned core Elo variables (K, MOV) for optimal weekly responsiveness.
- Outputs are translated into spreads, win probabilities, and EV-flagged picks.
(nfelo About; nfelo blog; greerreNFL GitHub)

**G-Elo / generalized Elo** (academic): models the *discretized margin of victory* directly rather than just win/loss, improving over binary Elo. Useful reference if extending Elo to predict full spread distributions. (arXiv 2010.11187)

### 1.2 Regression / GLMs
Logistic regression for win probability and linear/ridge regression for margin remain strong, interpretable baselines and are frequently *as good as* fancier models when features are well-engineered. Often best used as a layer on top of power ratings (e.g., regress margin on rating differential + situational features). Logistic regression sometimes wins specific sub-tasks (e.g., totals/over-under) even when trees win the main task. (xG Football Club; soccer ML survey)

### 1.3 Gradient-boosted trees (XGBoost / LightGBM / CatBoost) — the ML workhorse
Across sports-prediction literature, **gradient-boosted decision trees and tree ensembles consistently top the leaderboard** among ML methods, especially when paired with strong domain features (ratings, EPA). They handle nonlinear interactions (e.g., end-of-half clock/score/timeout interactions) far better than linear models. nflfastR explicitly **switched its EP/WP models to tree-based methods to improve calibration** in complicated nonlinear situations. (ML sports-betting survey arXiv 2410.21484; nflfastR Open Source Football; soccer Big Data study)

### 1.4 Neural nets / deep learning
Deep learning **has not consistently beaten gradient-boosted ensembles** in football outcome prediction, mostly due to limited data (a season is ~270 NFL / ~800+ FBS games) and weak temporal feature modeling. Use NNs only for specialized sub-models (e.g., tracking-data, sequence models) where data volume supports them. (ML survey; soccer ML review arXiv 2403.07669)

### 1.5 Bayesian / hierarchical models
Hierarchical (multilevel) Bayesian models are the principled answer to **small samples and uneven schedules** — exactly NCAAF's problem. **Partial pooling / shrinkage** interpolates between treating each team independently and treating all teams as identical, with the amount of shrinkage learned from data. This:
- Reduces overfitting and gives **honest uncertainty (credible intervals)**, which is essential for spreads and bankroll sizing.
- Lets a team's rating adjust quickly to real change (transfers, coaching) while not overreacting to one fluky game.
- Caveat: naive hierarchical models can **over-shrink** elite/terrible teams; fix with team-tier-specific priors or richer parameter structure — directly relevant to NCAAF's huge talent gap. (Baio & Blangiardo UCL; brms partial-pooling guide; Frontiers Bayesian football)

### 1.6 Ensembles / stacking — the practical winner
The best practical systems are **ensembles**: e.g., a voting/stacked blend of a power rating (Elo), a GBM, and a regression, then **blended with the market line**. Voting ensembles of Random Forest + XGBoost repeatedly post the highest accuracy in studies. The meta-lesson: combine an interpretable rating system (handles strength-of-schedule and recency) with a flexible learner (handles interactions), and anchor to the market. (Soccer ML studies; ML survey)

**Bottom line on approach:** Elo-style opponent-adjusted power rating + QB/situational adjustments, fed into (or ensembled with) a gradient-boosted model, calibrated and blended against the closing line. Bayesian hierarchical structure is the upgrade for NCAAF.

---

## 2. Key predictive features / signals

### 2.1 EPA (Expected Points Added) — the core efficiency signal
EPA values every play by its change in expected points given down, distance, field position, and time. It correlates with scoring/wins more strongly than yards because it is modeled directly on points. (nfelo EPA; PFF; nflfastR)

Actionable specifics:
- **Use opponent-adjusted EPA per play as a core team feature** (not raw EPA). Raw EPA measures *schedule + skill*; opponent adjustment isolates skill. A team can look elite purely from facing weak defenses. (Medium/Frost; CFBData primer)
- **Offensive EPA is "stickier" (more stable, more predictive) than defensive EPA.** A documented optimal weighting for predicting future net EPA is roughly **1.6× offensive EPA to 1.0× defensive EPA.** (nfelo EPA Tiers)
- EPA's weakness: single high-leverage plays (a goal-line fumble) can distort season EPA. Mitigate with **success rate** (binary play-success), **opponent adjustment**, and de-noising (e.g., dropping garbage-time plays). (nfelo)
- **Success rate** = fraction of plays gaining a "successful" share of needed yards by down. More stable / less volatile than EPA; use both. (PFF; CFBData)

### 2.2 DVOA-style opponent adjustment (the principle, not just the brand)
DVOA grades every play vs the league average **in that exact situation** (down/distance/field position — 5 yards on 3rd-and-4 ≠ 5 yards on 1st-and-10; red-zone weighted more), then **adjusts for opponent strength** iteratively. Defensive stats are offense-adjusted and vice versa. The takeaway for any model: **down-and-distance-aware play valuation + iterative opponent adjustment** is what separates predictive efficiency metrics from raw box-score stats. Football Outsiders' projection variant (DAVE) blends current-season DVOA with a preseason projection early in the year. (Football Outsiders methods/glossary; Covers)

### 2.3 Drive efficiency, pace/tempo
FEI rates teams on **opponent-adjusted possession (drive) efficiency** — points per possession over an average opponent — which neutralizes tempo. SP+ is explicitly **tempo- and opponent-adjusted**. Lesson: **normalize for pace** (use per-play and per-drive rates, not per-game totals) so fast/slow offenses are comparable. (FEI; SP+ via ESPN/CFBData)

### 2.4 QB-adjusted ratings
The largest single-player swing in football. Top systems maintain a **rolling QB rating (EPA + CPOE based)** and adjust team strength when the starter changes/injures. This is nfelo's headline improvement over 538. CPOE (completion % over expected) is a key QB-stability input. (nfelo QB model / nfeloqb GitHub; covers advanced metrics)

### 2.5 NCAAF preseason signals: returning production & recruiting
For preseason projections where there's no current-season data, SP+ projections are built from **three inputs**:
1. **Returning production** (how much of last year's production returns — now must include **transfer portal** in/out, not just departures).
2. **Recent recruiting** (multi-year recruiting rankings — proxy for raw talent and quality of replacements; now also incoming transfer quality).
3. **Recent program history** (prior-year SP+ — measures program "health"/baseline).
As the season progresses, current-season opponent-adjusted efficiency takes over. (SP+ via ESPN/CFBData; CFBData primer)

### 2.6 Situational / contextual features
- **Home-field advantage**: model it (varies by venue/altitude/travel) rather than a flat constant; ~+65 Elo / ~2-2.5 pts NFL historically, and **declining**. (538; nfelo HFA model)
- **Rest / bye / short weeks (Thursday)**, **travel distance / time zones**, **weather** (wind especially suppresses passing/totals), **injuries** (esp. QB and OL). All are standard adjustments in top models. (538 rest adjustment; CFBData; wunderdog)

---

## 3. NCAAF-specific challenges and how to handle them

College football is structurally harder than the NFL: ~130+ FBS teams, **enormous talent disparity**, **few cross-conference/inter-tier games** (sparse "connectivity" between teams), **FCS opponents** outside the rating pool, **massive annual roster turnover** (transfer portal), and **small samples** (12-15 games). (Dawg Post; BetMGM; SI/ESPN on portal churn; bettoredge)

Concrete mitigations:
- **Opponent adjustment is non-negotiable and harder.** With sparse inter-team connectivity, use iterative/network-style opponent adjustment (SP+/FEI style) or a Bayesian hierarchical structure that pools information across the schedule graph. Without it you are "modeling schedule, not skill." (CFBData primer)
- **Talent priors via recruiting** anchor teams with little shared schedule — recruiting/transfer talent composites give a prior on strength independent of results, crucial for connecting disjoint conference clusters. (SP+ methodology)
- **FCS games**: treat FCS opponents as a single (or few) low-rated pseudo-team(s) or down-weight/cap these games; don't let a 49-0 FCS win inflate a rating. (implied by SP+ handling; CFBData)
- **Blow-out / talent-gap spreads** (30-40+ pt favorites): use a **margin-of-victory cap or diminishing-returns multiplier** (as in Elo MOV) so beatdowns don't distort ratings, and recognize spread accuracy degrades at extremes. (BetMGM; andr3w321 MOV)
- **Garbage time**: filter or down-weight low-leverage plays (large win-probability deltas) when computing efficiency metrics, since backups distort EPA/success rate. (PFF "Defining Garbage Time")
- **Roster turnover**: heavier between-season mean reversion than NFL, and update returning-production/portal inputs; rebuild rosters "every January." (CollegeFootballPoll; SP+)
- **Small samples → wider uncertainty.** Report and use confidence intervals (Bayesian credible intervals); CFB models typically carry ±5-8% win-prob uncertainty. (bettoredge)
- **Data source**: `collegefootballdata.com` (CFBD) is the open community standard for CFB play-by-play, EPA, recruiting, and lines — the NCAAF analog to nflfastR. (CFBData blog)

---

## 4. Calibration to betting markets

### 4.1 The market (especially the closing line) is the benchmark
- The **closing line on a liquid market is the most accurate public probability estimate** because it aggregates all information and sharp money. (VSiN; BettorEdge; Trademate)
- **Pinnacle** is the reference sharp book: vig <~2% on liquid markets (vs ~8% elsewhere), high limits, welcomes sharps. Its **de-vigged closing line is the closest available proxy to true probability**, and other books move to follow it. (Pinnacle Odds Dropper; CompleteSports; SharkBetting)

### 4.2 Use the market two ways
1. **As a feature / prior.** Win-total and spread markets are excellent preseason and weekly priors. nfelo seeds preseason ratings from win-total markets and reverts toward the spread. **Blend model output with the market**, deviating only where confident.
2. **As the evaluation benchmark (CLV).** Track whether your pre-game number consistently beats the closing number. **Positive CLV is the strongest evidence your edge is real rather than variance**, and it predicts long-run profit better than win/loss record. (Buchdahl/Pinnacle; VSiN; BettorEdge)

### 4.3 De-vigging
To turn market odds into probabilities, **remove the vig**. Methods: multiplicative (proportional), additive, **Shin** (accounts for insider/sharp money — often preferred), and **power**. Choose by backtested calibration; Shin/power tend to be better than naive multiplicative on spreads/totals. (Pinnacle Odds Dropper de-vig guide; no-vig calculators)

### 4.4 Why beating the line is the real bar
The break-even ATS win rate at standard -110 is **~52.4%**. Documented edges over the market are thin: SP+ applied to spreads historically hits **~52-54% ATS**; classic anomalies (home underdogs) ~53.5%. So the realistic accuracy target is small but consistent CLV/ATS edge, not 60%+ win rates. (SP+ via CFBData; Skidmore/Claremont market-efficiency theses; Szalkowski & Nelson)

---

## 5. Backtesting & evaluation methodology

### 5.1 Use the right metrics
- **Brier score** — mean squared error between predicted probability and outcome (0/1). Overall probabilistic accuracy; decomposes into calibration + refinement. Lower is better.
- **Log loss (cross-entropy)** — penalizes confident wrong predictions hard; preferred when overconfidence is costly. Lower is better.
- **Calibration curves / reliability diagrams** — do events predicted at 70% happen ~70% of the time? **Calibration is among the best signals that an edge is real vs backtest noise.** (sports-ai.dev; DRatings; howtolearnML)
- **CRPS (Continuous Ranked Probability Score)** — the right metric for **spread/margin distributions**: generalizes MAE to full predictive distributions, rewarding forecasts that are both **sharp and calibrated**. Use CRPS (not just point MAE) when your model outputs a margin *distribution*. (TorchMetrics; EmergentMind CRPS)
- **ATS% and CLV** — the business metrics: cover rate vs the spread and average closing-line value. The ultimate validation.
- For margins: **MAE / RMSE** on predicted vs actual margin.

### 5.2 Validate like time series — no leakage
- **Walk-forward / expanding-window validation**, never random k-fold. Train on weeks 1..t, predict week t+1, then roll forward (train 1..t+1, predict t+2). This mirrors live use and **prevents using the future to predict the past.** Documented to cut overfitting materially (one NBA case: -22% overfit, ROI -2% → +5.4%). (oddsonnet; nxtbets; ggbettings)
- **Leakage traps to avoid**: season-long aggregates that include the game being predicted; opponent adjustments computed with full-season data leaking future games into past predictions (recompute adjustments using only data available at prediction time); using closing lines as features when you'd only have the opening line live; standardizing/imputing using full-dataset statistics.
- **Out-of-sample, multi-season testing** is mandatory given small per-season samples; report calibration on holdout seasons.
- **Benchmark every backtest against the closing line / market**, not just against coin-flip — beating raw accuracy is easy; beating the market is the test.

### 5.3 Profitability vs accuracy
The ML-betting literature repeatedly warns: **high predictive accuracy does not imply profit.** Models must be evaluated on calibrated probabilities turned into +EV bets vs the de-vigged market, with realistic vig and bet sizing (e.g., fractional Kelly). Calibration + positive CLV > raw accuracy. (ML survey arXiv 2410.21484; R-bloggers Kelly)

---

## 6. Concrete, actionable recommendations (synthesis)

1. **Build an Elo-style opponent-adjusted power rating** with: tuned K, a **diminishing-returns MOV multiplier** (correct for favorite-blowout autocorrelation), modeled (not flat) HFA, rest/travel/weather adjustments, and **between-season mean reversion** (heavier for NCAAF).
2. **Add a rolling QB-adjustment model** (EPA + CPOE based) — the highest-value single upgrade for NFL.
3. **Core features = opponent-adjusted EPA/play and success rate**, per-play and **per-drive** (pace-normalized), weighting **offense > defense (~1.6:1)**, with **garbage-time filtering**.
4. **For NCAAF preseason**, project from **returning production (incl. transfer portal) + recruiting/transfer talent composites + prior-year rating**; handle FCS games and sparse connectivity via talent priors and a **Bayesian hierarchical structure with tier-aware priors** to avoid over/under-shrinkage; cap MOV on blowouts.
5. **Ensemble**: blend the power rating with a **gradient-boosted model** (interactions) and **anchor to the de-vigged market line** (Shin/power de-vig); deviate from market only with confidence.
6. **Evaluate** with **walk-forward CV**, **Brier + log loss + calibration curves** for win prob, **CRPS** for margin distributions, and **ATS% + CLV** as the real bar. Recompute all opponent adjustments using only pre-game data to avoid leakage.
7. **The goal is consistent positive CLV / ~52.5-54% ATS**, not implausible win rates. Calibration and beating the closing line are the truth signals.

---

## Sources

Power ratings / Elo / nfelo:
- FiveThirtyEight, "How Our NFL Predictions Work" — https://fivethirtyeight.com/methodology/how-our-nfl-predictions-work/
- nfelo, About — https://www.nfeloapp.com/about/
- nfelo, NFL Power Ratings — https://www.nfeloapp.com/nfl-power-ratings/
- nfelo, EPA Tiers — https://www.nfeloapp.com/nfl-power-ratings/nfl-epa-tiers/
- nfelo, "What are EPA in the NFL" — https://www.nfeloapp.com/analysis/expected-points-added-epa-nfl/
- nfelo, QB Rankings — https://www.nfeloapp.com/qb-rankings/
- greerreNFL/nfelo (GitHub) — https://github.com/greerreNFL/nfelo
- greerreNFL/nfeloqb (QB model) — https://github.com/greerreNFL/nfeloqb
- Model 284 NFL Elo methodology — https://model284.com/model-284-nfl-elo-ratings-methodology/
- andr3w321, Elo MOV adjustments — https://andr3w321.com/elo-ratings-part-2-margin-of-victory-adjustments/
- G-Elo (discretized MOV), arXiv 2010.11187 — https://arxiv.org/pdf/2010.11187

Efficiency metrics (EPA / DVOA / success rate / FEI / SP+):
- Football Outsiders, What is DVOA / methods — https://www.footballoutsiders.com/info/methods
- Football Outsiders glossary — https://www.footballoutsiders.com/info/glossary
- Covers, NFL advanced metrics (DVOA/EPA/CPOE) — https://www.covers.com/nfl/key-advanced-metrics-betting-tips
- PFF, betting metrics (success rate, EPA) — https://www.pff.com/news/bet-nfl-bet-terms-metrics-game-script-handicapping-success-rate-epa
- PFF, Defining Garbage Time — https://www.pff.com/news/defining-garbage-time
- Reuben Frost, Opponent-Adjusted Team Rankings (EPA) — https://medium.com/@reuben.j.frost/opponent-adjusted-team-rankings-c0c899af8ac5
- EPA / expected points (academic), arXiv 2409.04889 — https://arxiv.org/pdf/2409.04889
- ESPN, 2026 SP+ rankings (Bill Connelly methodology) — https://www.espn.com/college-football/story/_/id/48306284/2026-college-football-sp+-rankings-138-fbs-teams
- Our Daily Bears, Primer on Predictive Statistics in CFB — https://www.ourdailybears.com/baylor-bears-football/2022/8/1/23271366/predictive-statistics-in-cfb-primer

ML approaches:
- Systematic review of ML in sports betting, arXiv 2410.21484 — https://arxiv.org/pdf/2410.21484
- ML for soccer match prediction (review), arXiv 2403.07669 — https://arxiv.org/pdf/2403.07669
- Data-driven soccer prediction (Springer Big Data) — https://link.springer.com/article/10.1186/s40537-024-01008-2
- Which ML models perform best for football prediction (Substack) — https://thexgfootballclub.substack.com/p/which-machine-learning-models-perform
- Forecasting CFB outcomes with modern modeling (South & Egros, JSA 2020) — https://content.iospress.com/articles/journal-of-sports-analytics/jsa190314

Bayesian / hierarchical:
- Baio & Blangiardo, Bayesian hierarchical model for football (UCL) — https://discovery.ucl.ac.uk/16040/1/16040.pdf
- brms partial pooling guide (R-bloggers) — https://www.r-bloggers.com/2026/03/how-to-fit-hierarchical-bayesian-models-in-r-with-brms-partial-pooling-explained/
- Frontiers, Bayesian approach to predict football performance — https://www.frontiersin.org/journals/sports-and-active-living/articles/10.3389/fspor.2025.1486928/full

Open-source data / community:
- nflfastR — https://nflfastr.com/
- nflverse (GitHub) — https://github.com/nflverse
- Open Source Football: nflfastR EP/WP/CP models — https://opensourcefootball.com/posts/2020-09-28-nflfastr-ep-wp-and-cp-models/
- CollegeFootballData (CFBD) modeling tips — https://blog.collegefootballdata.com/college-football-modeling-tips/
- greerreNFL/nfl_cover_probability — https://github.com/greerreNFL/nfl_cover_probability

Market calibration / CLV / de-vig:
- VSiN, importance of closing line value — https://vsin.com/how-to-bet/the-importance-of-closing-line-value/
- BettorEdge, what is CLV — https://www.bettoredge.com/post/what-is-closing-line-value-in-sports-betting
- Buchdahl, CLV demystified (Pinnacle Odds Dropper) — https://www.pinnacleoddsdropper.com/blog/closing-line-value--clv-demystified-by-expert-joseph-buchdahl
- Pinnacle Odds Dropper, de-vig methods (multiplicative/additive/Shin/power) — https://www.pinnacleoddsdropper.com/guides/how-to-devig-pinnacle-s-odds-for-betting-on-soft-books
- How Pinnacle sets the sharpest lines — https://www.completesports.com/how-pinnacle-sets-the-sharpest-lines/
- Trademate, closing line is the most important metric — https://tradematesports.medium.com/closing-line-the-most-important-metric-in-sports-trading-58e56cdb4458
- NFL betting market efficiency (Skidmore) — https://creativematter.skidmore.edu/cgi/viewcontent.cgi?article=1093&context=econ_studt_schol
- NFL moneyline market efficiency (Claremont) — https://scholarship.claremont.edu/cgi/viewcontent.cgi?article=5145&context=cmc_theses
- Performance of betting lines for NFL outcomes, arXiv 1211.4000 — https://arxiv.org/pdf/1211.4000

Evaluation / backtesting:
- AI model calibration: Brier score & reliability — https://www.sports-ai.dev/blog/ai-model-calibration-brier-score
- DRatings, Log Loss vs Brier Score — https://www.dratings.com/log-loss-vs-brier-score/
- Brier score explainer — https://howtolearnmachinelearning.com/articles/brier-score/
- CRPS (TorchMetrics) — https://lightning.ai/docs/torchmetrics/stable/regression/crps.html
- CRPS overview (EmergentMind) — https://www.emergentmind.com/topics/continuous-ranked-probability-score-crps
- Cross-validation for betting models (walk-forward) — https://oddsonnet.com/news/mastering-cross-validation-techniques-for-betting-models-avoid-overfitting-and-boost-profits
- Build a Winning NFL Betting Model — https://nxtbets.com/winning-nfl-betting-model/
- Designing sports betting systems in R (Kelly/EV) — https://www.r-bloggers.com/2026/02/designing-sports-betting-systems-in-r-bayesian-probabilities-expected-value-and-kelly-logic/

NCAAF-specific challenges:
- Why CFB betting differs from NFL (Dawg Post) — https://dawgpost.com/s/7812/why-betting-on-college-football-isnt-the-same-as-the-nfl
- BetMGM, CFB betting trends/strategies — https://sports.betmgm.com/en/blog/college-football/college-football-betting-trends-strategies-bm06/
- How win probability models work in CFB (BettorEdge) — https://www.bettoredge.com/post/how-win-probability-models-work-in-college-football
- How the transfer portal changed roster building — https://www.collegefootballpoll.com/news/how-the-transfer-portal-changed-college-football-roster-building/
