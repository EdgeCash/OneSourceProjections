# Modeling, Calibration & Evaluation Techniques for Sports-Betting Projection Models

Focus: NFL / NCAAF point spreads, totals, win probabilities, and player props.
Research date: 2026-06-24.

This report covers the modeling, calibration, ensembling, edge-selection, and
evaluation techniques that separate genuinely profitable projection models from
mediocre ones, with concrete formulas, numeric anchors, and source URLs.

---

## 0. The single most important framing

A betting model's job is **not** to predict the winner or the exact score. Its
job is to output a **well-calibrated probability distribution** over outcomes
that can be compared against the market's (devigged) probability to find +EV
spots. Two consequences flow from this and drive everything below:

1. **Calibration > accuracy.** A model that is 55% accurate but says "70%" when
   it's really 55% will lose money. A model that is only 52% accurate but whose
   stated probabilities match reality will make money on the right side of the
   vig. (See §2.)
2. **The market is the strongest prior.** The closing line is the most accurate
   public forecast that exists. Any model that ignores it is starting from a
   worse baseline than free information. The edge comes from *small, calibrated
   deviations* from the market, not from beating it from scratch. (See §3.)

---

## 1. Spread / Total / Score Modeling — predict a distribution, not a point

### 1.1 Margin of victory as a (roughly) normal distribution

The canonical NFL result: **final margin ≈ Normal(mean = point spread, σ ≈ 13–14).**

- Winston/Stern estimate **σ = 13.86**; more recent (1978–2012) data gives
  **σ ≈ 13.45**. A commonly cited regression gives **σ ≈ 13.59** for the
  spread→win-prob mapping.
- This means the spread itself is the unbiased estimate of the mean margin —
  another reason to anchor on the market (§3).

**Spread → win probability** (favorite by `s` points):

```
P(win) = 1 - Φ((0.5 - s) / σ)        # using a 0.5 continuity correction for the tie/hook
       = Φ(s / σ)                     # simpler, ignoring the half-point
```

Excel form (σ = 13.86):  `P(win) = (1 - NORMDIST(0.5, s, 13.86, TRUE)) + 0.5`
Closed form: `Pr(Fav beats Dog | spread = s) = Φ(s / 13.59)`.

**Spread → cover probability** for a bet at line `L` when your model projects
mean margin `m`:

```
P(cover) = 1 - Φ((L - m) / σ)        # P(actual margin > L)
```

The *edge* of any spread model is the gap between your projected `m` and the
market line `L`, scaled by σ. A 1-point edge with σ=13.5 is worth only
~Φ(0.5/13.5) − 0.5 ≈ **1.5 percentage points** of cover probability — illustrating
why edges are small and why miscalibration destroys them.

### 1.2 Key numbers — why a pure normal is wrong

NFL scoring is lumpy (3 = FG, 7 = TD+XP), so the margin distribution has **spikes
(local maxima) at ±3, ±7, ±10** and dips between them. Empirical frequencies:

| Margin | Approx. % of games |
|--------|--------------------|
| 3      | ~11.8% (most common) |
| 7      | ~7.4% |
| 10     | ~5.7% |
| 3 + 7 together | ~19.2% |
| 3 + 7 + 10 | ~25% (a quarter of all games) |
| Top key numbers combined | >40% of decisive margins |

Implications for modeling:
- A plain Gaussian **underestimates** the probability mass exactly at 3 and 7
  and **overestimates** it at 4, 5, 6. Cover/push probabilities near key numbers
  must be computed from the **empirical margin distribution**, not the normal CDF.
- "Buying the hook" (3 → 2.5 or 3.5) and getting on/off 3 is where the most line
  value lives. Models should explicitly value half-points around key numbers.
- The longer extra point (since 2015) shifted some mass; key-number weights need
  periodic re-estimation. NCAAF has flatter, wider distributions (more blowouts,
  σ larger) so key numbers matter less than in the NFL.

**Better-than-Gaussian approaches (nfelo-style):**
- Build the margin distribution as a **mixture**: a smooth base distribution
  (e.g. a "super-Gaussian" / generalized normal) plus weighted spikes at each
  key number, where the spike weight **decays with distance from the projected
  spread**. Calibrate the spike weights to thousands of historical games.
- Add a **binary-outcome multiplier** that overweights the "other side" of zero
  (a favorite is more likely to at least win than the symmetric tail implies).
- Production tool: nfelo's "Margin Probabilities from NFL Spreads" / Cover
  Probability Calculator does exactly this (empirical optimization over historical
  spread/margin pairs).

### 1.3 Totals and direct score modeling

- **Poisson** is the baseline for scoring-event counts (used heavily in soccer
  goals; for football, applied to drives/TDs/FGs or adapted to points).
- **Negative binomial** handles **over-dispersion** (variance > mean). In
  practice over-dispersion in scoring is small enough that the added complexity
  often isn't worth it — prefer it only if you see clear over-dispersion.
- **Skellam distribution** = the distribution of the *difference* of two Poisson
  variables. It models the margin (goal/point difference) directly and naturally
  handles negative correlation between the two teams' scoring — useful for
  spreads/moneylines without separately simulating each team. **Skellam
  regression** lets covariates drive the two rates.
- **Bivariate Poisson / Dixon–Coles** correct the independence assumption and
  the low-score correlation (the classic refinement family).
- **Monte Carlo simulation** is the general-purpose alternative: simulate each
  team's scoring process N times, then read off P(cover), P(over), win prob, and
  any derived market from the simulated outcome distribution. Time-dynamic models
  favor Bayesian estimation + MCMC; static models use MLE.

### 1.4 Player props — distributions, not means

- Props require the **full predictive distribution** of a player stat, because
  the bet is `P(stat > line)`, not the mean. A correct mean with the wrong
  variance/shape is worthless.
- Count props (receptions, completions, TDs): Poisson / negative binomial.
- Continuous-ish props (rushing/receiving yards): often modeled as a Gamma or
  log-normal, or via Monte Carlo over per-attempt outcomes (attempts × yards/att,
  each with its own distribution).
- Volume props are highly **conditional** (game script, pace, injuries to the
  depth chart) — these drive variance and must be inputs, not afterthoughts.
- Correlation matters for parlays/SGPs: a player's receptions and yards are
  positively correlated; modeling them independently mis-prices combos.

---

## 2. Probability Calibration — the highest-leverage step

A model can rank games well (good AUC) yet output probabilities that are
systematically over/under-confident. For betting, **the absolute probability is
the product**, so calibration is non-negotiable.

### 2.1 Diagnostics

- **Reliability diagram / calibration curve:** bin predictions (e.g. deciles),
  plot mean predicted prob (x) vs observed frequency (y). Perfect calibration =
  the 45° diagonal. Above the line = under-confident; below = over-confident.
- **Expected Calibration Error (ECE):** weighted average |predicted − observed|
  across bins. Target **ECE < 0.05** (strong).
- **Brier score** (mean squared error of probabilities): `(1/N) Σ (p_i − y_i)²`,
  bounded [0,1], lower is better. **< 0.20 is good** for binary sports outcomes.
  Brier decomposes into reliability (calibration) + resolution − uncertainty.
- **Log loss / log score:** `−(1/N) Σ [y log p + (1−y) log(1−p)]`. Punishes
  confident wrong predictions much harder than Brier (unbounded). Use Brier when
  you want stability/robustness to outliers; use log loss when you want to
  penalize over-confidence aggressively (it is what proper bettors fear most).

### 2.2 Calibration methods

- **Platt scaling:** fit a logistic regression `1 / (1 + exp(a·f + b))` mapping
  raw scores `f` to calibrated probabilities. Parametric, needs little data,
  fast (good for nightly refresh), but assumes an **S-shaped (sigmoid)**
  distortion — can underfit complex miscalibration. Best for simpler base models
  (logistic regression, small models).
- **Isotonic regression:** non-parametric, fits any **monotonic** mapping. More
  flexible, corrects arbitrary monotone distortion, but **needs more data and
  overfits when data is scarce**. Best for flexible base models (XGBoost, neural
  nets) when you have enough holdout data.
- **Beta calibration / temperature scaling** are additional options (temperature
  scaling = single-parameter softmax scaling, common for NNs).

**Critical rules:**
1. **Never calibrate on the training set.** Use a separate, time-disjoint
   holdout (or cross-validated calibration / `CalibratedClassifierCV`). Calibrating
   on training data manufactures false confidence and is itself a leakage bug.
2. Re-check calibration **over time** — drift (rule changes, roster turnover,
   market shifts) degrades calibration season to season.
3. Calibrate the thing you bet on. If you bet cover probabilities, calibrate
   cover probabilities against actual cover outcomes.

---

## 3. Ensembling, Stacking & Treating the Market as a Prior

### 3.1 Why blend at all

No single source is best across all games. Robust models **stack**: power ratings
(Elo and variants), feature-based ML (XGBoost / GBMs / NNs over team & player
stats), and **market consensus**. Blending reduces variance and overfitting.

### 3.2 The market is a strong prior — regress toward it

- The **closing line is the single best public predictor** of outcome (better
  than opening or mid-week lines). NFL betting markets are highly efficient.
- Empirically (nfelo "Using Market Regression to Improve Prediction Accuracy"):
  **regressing a model's spread toward the market spread produces a model that
  reliably beats the closing line** more often than the raw model alone. The
  market absorbs information (injuries, weather, sharp money) faster than most
  feature pipelines.
- Practical form — a shrinkage / linear blend:

  ```
  spread_final = w · spread_model + (1 − w) · spread_market
  ```

  with `w` typically **small** (often 0.1–0.3 weight on your own model) and tuned
  by walk-forward CLV/Brier. This is **shrinkage toward the market prior**:
  trust your model only to the extent it has demonstrated out-of-sample edge.
- Bayesian framing: treat the devigged market probability as the prior, your
  model's signal as the likelihood; the posterior is your bet probability.
  Empirical-Bayes shrinkage formalizes how far to pull team/parameter estimates
  toward the population (or market) mean given their uncertainty.

### 3.3 Stacking mechanics

- Generate base-model predictions out-of-fold (time-respecting), then train a
  **meta-learner** (often a simple regularized logistic/linear model) on those
  predictions + the market line. Keep the meta-learner simple to avoid
  re-overfitting. Then calibrate the stacked output (§2).

---

## 4. Edge & Bet Selection

### 4.1 Devig the market to get the "true" probability

Sportsbook implied probabilities sum to >100% (the vig/overround). Convert and
remove it:

- Implied prob from American odds: `+odds → 100/(odds+100)`;
  `−odds → odds/(odds+100)`. Decimal: `1/decimal`.
- **Multiplicative (proportional) devig** (two-way):
  `p_fair_A = impl_A / (impl_A + impl_B)`. Allocates vig proportional to implied
  prob. Most common default.
- **Additive devig:** subtract the overround equally across outcomes. Corrects
  favorite-longshot bias somewhat but can yield negative probs for big dogs.
- **Shin method:** iterative, models "insider"/informed money and distributes vig
  unevenly; generally more predictive than multiplicative for longshots
  (equivalent to additive for two outcomes... — note: in practice Shin and
  multiplicative diverge most at the extremes). **Power method** is another option.
- Use a **sharp book** (e.g. Pinnacle, or the consensus closing line) as the
  reference for the fair probability — soft-book lines are noisier.

### 4.2 Expected value and edge

```
EV (per $1 stake, decimal odds d) = p · (d − 1) − (1 − p)
Edge (%) = p − p_fair_market          # your prob minus devigged market prob
```

Only bet when your **calibrated** `p` exceeds the devigged market `p_fair` by
enough to overcome residual uncertainty (use a threshold, e.g. require ≥ ~2–3%
edge, to avoid betting noise).

### 4.3 Kelly staking

Full Kelly fraction of bankroll:

```
f* = (b·p − q) / b = (p·(d−1) − (1−p)) / (d−1)
```

where `b = d − 1` (net decimal odds), `p` = your win prob, `q = 1 − p`.

- **Use fractional Kelly.** Full Kelly is theoretically growth-optimal but
  extremely volatile and unforgiving of probability error. Practitioner consensus
  and research favor **0.25–0.5 Kelly** (half-Kelly is common). One reviewed study
  recommends **0.5 Kelly with a conservative ~10% edge threshold** as the most
  profitable practical strategy (caps variance, avoids ruin).
- Because your `p` is itself uncertain, fractional Kelly is also a hedge against
  model miscalibration — another reason calibration (§2) and staking interact.
- Cap single-bet exposure (e.g. ≤ 1–5% of bankroll) regardless of Kelly output.

### 4.4 CLV — the gold-standard feedback metric

- **Closing Line Value:** did you beat the closing line? If you consistently bet
  a number better than where the line closes, you are extracting value the market
  later agreed with — the **best available leading indicator of long-run edge**,
  far less noisy than win/loss results.
- Measure CLV in **devigged-probability terms** (your line's fair prob vs the
  closing fair prob), not just price, to compare across markets.
- A model can be validated on CLV with **far fewer events** than it takes to
  validate on realized ROI, because CLV strips out outcome variance.

---

## 5. Evaluation & Backtesting

### 5.1 Walk-forward (time-series) validation

- **Never use random k-fold CV** on sequential sports data — it leaks future
  into the past. Use **walk-forward / expanding-window**: train on weeks 1…t,
  predict week t+1, roll forward. This mirrors live deployment.
- **Avoid lookahead/leakage rigorously:** every feature must use only information
  that existed **before kickoff** (real timestamps). Common leaks: using
  season-long stats that include the game being predicted, closing lines as a
  feature when you'd only have the open, post-game injury status, opponent-adjusted
  ratings computed over the full season.
- A 2022 Journal of Sports Analytics finding: models without proper CV
  **overstated accuracy by up to ~15%** due to overfitting/leakage.

### 5.2 Proper scoring rules (what to optimize and report)

- **Brier score** and **log loss** (§2.1) for binary markets (win, cover, over).
- **CRPS (Continuous Ranked Probability Score)** for the *full predictive
  distribution* (margins, totals, prop yardages): measures the squared distance
  between the predicted CDF and the step function at the realized outcome —
  generalizes Brier to continuous/distributional forecasts. Use CRPS to evaluate
  whether your *distribution* (not just the mean) is right — critical for spreads
  near key numbers and for props.
- All three are **strictly proper** (minimized only by truthful forecasts), so
  they can't be gamed by shading probabilities.

### 5.3 Betting-specific metrics

- **ATS record vs. break-even.** At standard −110 juice, break-even is
  **52.38%** (`110 / 210`). Beating ~52.4% ATS is the threshold for profit;
  ~53–55% sustained is excellent. For moneyline/props, compute the break-even
  from the actual price.
- **ROI / yield** per bet. Be **suspicious of large backtest ROI**: pros target
  ~**3–8% ROI**; a backtest showing 20–30% over a big sample almost always
  signals overfitting or leakage.
- **CLV** over the backtest (§4.4) — the most trustworthy single number.
- Track **calibration over time**, not just aggregate.

### 5.4 Skill vs. luck — sample size & significance

- Variance is huge. **50 games:** a 60% rate is easily luck. **5,000 games:**
  even 52.5% is meaningful. Rough floor of ~**139 bets** before significance is
  even discussable; practitioners want **300–500+ bets across multiple seasons**
  before trusting a system.
- Test the realized win rate against the break-even with a binomial/one-sample
  proportion test (or t-test on per-bet profit) to get a p-value; report a
  **confidence interval on ROI**, not a point estimate.
- Prefer CLV-based validation when the realized-result sample is small.

---

## 6. Common Pitfalls (and the fixes)

| Pitfall | Why it kills models | Fix |
|---------|--------------------|-----|
| **Overfitting / curve-fitting** | Learns noise; great in-sample, bad live | Keep to ~3–5 core variables; regularize; walk-forward; be wary of strategies needing many conditions to trigger |
| **Data leakage / lookahead** | Inflates accuracy ~15% | Strict pre-kickoff timestamps; no full-season features when predicting mid-season; chronological simulation |
| **Calibrating on training data** | Manufactured confidence | Always a time-disjoint holdout / CV-calibration |
| **Ignoring the vig** | "Edges" vanish after juice | Compare to **devigged** market prob; break-even 52.4% at −110 |
| **Chasing in-sample ATS%** | Sample mining → false positives | Out-of-sample only; demand large N; report CI |
| **Survivorship / selection bias** | Backtest only includes lines/games that "worked" or are still available | Use point-in-time line snapshots; include all games |
| **Point estimate instead of distribution** | Can't price cover/over/prop correctly | Model σ and key numbers; use CRPS to check the distribution |
| **Ignoring the market** | Starts below a free baseline | Shrink toward the closing line; validate on CLV |
| **Full Kelly** | Ruin-level variance, intolerant of error | Fractional (0.25–0.5) Kelly + exposure caps |

---

## 7. A reference pipeline (putting it together)

1. **Generate** team/player power ratings (Elo + ML) → projected mean margin /
   total / prop mean.
2. **Distribution:** map to a full predictive distribution — normal-with-σ plus
   key-number spikes (spreads), Poisson/NB/Skellam or Monte Carlo (scores),
   count/continuous distribution (props).
3. **Blend** with the **devigged market** via shrinkage (`w` small, tuned on
   walk-forward CLV).
4. **Calibrate** the output probabilities (Platt or isotonic) on a time-disjoint
   holdout; verify with reliability diagram, ECE < 0.05, Brier < 0.20.
5. **Devig** the live market, compute **edge = p − p_fair** and **EV**.
6. **Stake** with fractional Kelly + edge threshold + exposure cap.
7. **Evaluate** walk-forward: CRPS/Brier/log loss, ATS vs 52.4%, ROI with CI,
   and above all **CLV** over time; demand 300–500+ bets before trusting it.

---

## Sources

**Spread / margin / key numbers**
- nfelo — Margin Probabilities from NFL Spreads: https://www.nfeloapp.com/analysis/margin-probabilities-from-nfl-spreads/
- nfelo — NFL Cover Probability Calculator: https://www.nfeloapp.com/tools/nfl-cover-probability-calculator/
- Converting College Football Point Spread Differentials to Probabilities (arXiv): https://arxiv.org/abs/2212.08116
- Pro-Football-Reference Win Probability Model: https://www.pro-football-reference.com/about/win_prob.htm
- The Performance of Betting Lines for Predicting NFL Outcomes (arXiv): https://arxiv.org/pdf/1211.4000
- Pinnacle — NFL key numbers betting: https://www.pinnacle.com/en/betting-articles/football/nfl-key-numbers-betting/b4p2cfb59zen79n7
- Action Network — NFL key betting numbers / margins: https://www.actionnetwork.com/nfl/nfl-key-betting-numbers-spread-margins-of-victory-line-value
- Covers — NFL Key Numbers: https://www.covers.com/nfl/key-numbers
- Washington Post — NFL most common margin of victory: https://www.washingtonpost.com/sports/2022/09/20/nfl-margin-victory-point-spreads/
- Predicting Point Spread in NFL Games (Stanford CS229): https://cs229.stanford.edu/proj2016/report/WadsworthVera-PredictingPointSpreadinNFLGames-report.pdf

**Score / count distributions**
- Bayesian modelling of football outcomes using Skellam's distribution: https://www.researchgate.net/publication/228621612_Bayesian_modelling_of_football_outcomes_Using_the_Skellam's_distribution_for_the_goal_difference
- A Skellam Regression Model for Quantifying Positional Value (arXiv): https://arxiv.org/pdf/1807.07536
- Poisson Distribution in Football goal modelling (Tactiq): https://www.tactiq.club/en/blog/poisson-distribution-goal-modelling-football/
- Bayesian state-space models for EPL (RSS Series C): https://academic.oup.com/jrsssc/article/74/3/717/7929974

**Calibration**
- AI Model Calibration for Sports Betting — Brier & Reliability (sports-ai.dev): https://www.sports-ai.dev/blog/ai-model-calibration-brier-score
- Betting Model Calibration Techniques (Underdog Chance): https://www.underdogchance.com/betting-model-calibration-techniques/
- How Calibration Supercharges Your AI Sports Betting Model (SportbotAI): https://www.sportbotai.com/blog/calibration-ai-sports-betting-model-1775671361692
- Calibrated Classification Model with scikit-learn (ML Mastery): https://machinelearningmastery.com/calibrated-classification-model-in-scikit-learn/
- Predicting Good Probabilities With Supervised Learning (Niculescu-Mizil & Caruana, Cornell): https://www.cs.cornell.edu/~alexn/papers/calibration.icml05.crc.rev3.pdf
- Probability Calibration in ML (Train in Data): https://www.blog.trainindata.com/probability-calibration-in-machine-learning/

**Scoring rules / evaluation**
- Scoring rule (Wikipedia): https://en.wikipedia.org/wiki/Scoring_rule
- Log Loss vs. Brier Score (DRatings): https://www.dratings.com/log-loss-vs-brier-score/
- Evaluating Probabilistic Predictions: Proper Scoring Rules: https://huiwenn.github.io/predictive-distributions
- MVG-CRPS robust loss for probabilistic forecasting (arXiv): https://arxiv.org/pdf/2410.09133

**Ensembling / market as prior**
- nfelo — Using Market Regression to Improve Prediction Accuracy in the NFL: https://www.nfeloapp.com/analysis/using-market-regression-to-improve-prediction-accuracy-in-the-nfl/
- Ensemble Modeling in Sports (Harvard Science Review): https://harvardsciencereview.org/2025/10/01/ensemble-modeling-in-sports-combining-algorithms-for-stronger-predictions/
- Mastering the Elo Rating System (SignalOdds): https://signalodds.com/blog/mastering-the-elo-rating-system-for-smarter-sports-betting
- How to Use Power Ratings to Create Point Spreads (Underdog Chance): https://www.underdogchance.com/how-to-use-power-ratings-to-create-your-own-point-spreads/
- Improving pairwise comparison models using Empirical Bayes shrinkage (arXiv): https://arxiv.org/pdf/1807.09236

**Edge, devig, Kelly, CLV**
- Devigging Sportsbook Odds (Datawise Bets): https://www.datawisebets.com/blog/devigging-sportsbook-odds
- Devigging Methods Explained: Power, Shin, Additive, Multiplicative (Bet Hero): https://betherosports.com/blog/devigging-methods-explained
- How to de-vig Pinnacle's odds (Pinnacle Odds Dropper): https://www.pinnacleoddsdropper.com/guides/how-to-devig-pinnacle-s-odds-for-betting-on-soft-books
- Kelly Criterion Calculator (OddsJam): https://oddsjam.com/betting-calculators/kelly-criterion
- An Investigation of Sports Betting Selection and Sizing (Wharton): https://wsb.wharton.upenn.edu/wp-content/uploads/2023/05/Beggy_2023__Betting_Kelly.pdf
- Optimal sports betting strategies in practice: an experimental review (arXiv): https://arxiv.org/pdf/2107.08827
- A statistical theory of optimal decision-making in sports betting (PMC): https://pmc.ncbi.nlm.nih.gov/articles/PMC10306238/
- Kelly Criterion revisited: optimal bets (arXiv): https://arxiv.org/pdf/physics/0607166

**Backtesting / pitfalls**
- How to Backtest a Sports Betting Strategy Without Overfitting (Great Bets): https://www.greatbets.co.uk/how-to-backtest-a-sports-betting-strategy-without-overfitting/
- 7 Mistakes with Overfitting Betting Models (Predictology): https://www.predictology.co/blog/7-mistakes-youre-making-with-overfitting-betting-models-and-how-to-fix-them/
- How to Avoid Backtesting Pitfalls in Football Betting (Predictology): https://www.predictology.co/blog/how-to-avoid-the-biggest-backtesting-pitfalls-in-football-betting/
- Why Backtesting Matters (BALLDONTLIE): https://www.balldontlie.io/blog/why-backtesting-matters/
- Cross-Validation Techniques for Betting Models (OddsOnNet): https://oddsonnet.com/news/mastering-cross-validation-techniques-for-betting-models-avoid-overfitting-and-boost-profits
- Designing Sports Betting Systems in R: Bayesian, EV, Kelly (R-bloggers): https://www.r-bloggers.com/2026/02/designing-sports-betting-systems-in-r-bayesian-probabilities-expected-value-and-kelly-logic/
