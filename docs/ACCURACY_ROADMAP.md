# Accuracy Roadmap — NFL/NCAAF, road to live for the 2026 season

Derived from the research synthesis (`docs/research/00-synthesis.md`) and the
file-by-file audit of the engine. Ordered by **impact ÷ effort**. The betting/edge
layer is already strong; nearly all the accuracy upside is in the model **inputs**.

Discipline (inherited from the existing football model): **prove the edge in the
walk-forward backtest before wiring anything into live projections.** Each item
below ships behind validation, not as an assertion.

Legend: ✅ done in this consolidation · 🔜 next · ⏳ later · effort S/M/L

---

## Stage 0 — Foundations (this consolidation) ✅
- ✅ **EPA ratings engine** — `project547/epa.py`: opponent-adjusted EPA/play +
  success rate via ridge regression with team dummies; offense weighted 1.6×;
  garbage-time filter; EPA→points conversion. Pure, unit-tested.
- ✅ **nflverse PBP loader** — `project547/clients/nflverse.py`: season
  play-by-play → opponent-adjusted team EPA (free, no key).
- ✅ **CFBD client** — `project547/clients/cfbd.py`: NCAAF PPA(EPA), SP+/FPI,
  returning production, talent, lines, games (free key via `CFBD_API_KEY`).
- ✅ Research + inventory captured in `docs/`.

## Validation log — EPA ratings (Stage 1 gate)

Walk-forward on nflverse PBP via `scripts/validate_epa.py`, NFL games week ≥ 5,
**6 seasons (2019–2024, n=1217)**, models scored online (expanding window):

| Model | Brier ↓ | LogLoss ↓ | MarginMAE | Win% |
|---|---|---|---|---|
| Points (raw, current model) | 0.2254 | 0.6419 | 10.35 | 64.2 |
| EPA (raw) | 0.244 | 0.726 | 12.3 | 62.6 |
| Stack (points + aggregate EPA) | 0.2257 | 0.6431 | 10.39 | 62.8 |
| **Stack (points + QB-EPA diff)** | **0.2250** | **0.6416** | 10.36 | 63.8 |

Two findings, both decisive:

**1. Aggregate team EPA is a dead end (kept OFF, `epa_blend=0`).** The learned
stacker weights points ≈ 1.06 and aggregate-EPA ≈ 0.06 — essentially zero — and
collapses back to the points model. Robust across every ridge `lam` ∈
{5,10,25,50,150}. At the team-season level, points-for/against already encode
what aggregate EPA does. Not a "needs one more tweak" — it's a clean negative.

**2. QB-adjusted EPA DOES beat the baseline ✅.** Adding a **primary-QB
passing-EPA differential** (home starter's rolling pass-EPA − away's) as a
stacking feature improves both Brier (0.2254 → 0.2250) and LogLoss
(0.6419 → 0.6416) out-of-sample over 6 seasons, with a real learned weight
(qb_epa_diff = 4.48 vs aggregate-EPA's 0.06). This confirms the research: EPA's
signal lives in the **QB-adjusted** form, exactly the highest-value NFL lever.

The gain is **modest** with a crude QB feature (primary passer's raw mean EPA, no
opponent adjustment, no CPOE, no sample-size shrinkage) — so it validates the
direction, not a finished model. To turn it on in production:
- **Build out the QB signal** (Stage 4, item 10): opponent-adjust QB EPA, add
  CPOE, shrink by attempts, and handle in-season starter changes/injuries — each
  should grow the edge beyond this proof-of-concept.
- **Plumb live QB data**: identify each team's projected starter per slate
  (depth charts) and maintain rolling per-QB EPA from the in-season PBP feed.
- Re-clear this gate, then set `epa_blend > 0` (or wire the QB term directly).

`scripts/validate_epa.py` is the gate every future variant must beat.

### QB signal, step 1 — opponent-adjust + shrink (Stage 4 item 10, in progress)

`epa.passer_epa_ratings` replaces the crude proxy with a ridge that rates the
**passer** (not the team) adjusted for the defenses faced and shrunk toward
league average by dropbacks; `scripts/validate_qb_epa.py` is the bake-off.
Walk-forward, 6 seasons (2019–2024, n=1217, week ≥ 5):

| Model | Brier ↓ | LogLoss ↓ |
|---|---|---|
| Points (raw, current) | 0.2254 | 0.6419 |
| Stack (points + QB **crude** proxy) | 0.2250 | 0.6416 |
| Stack (points + QB **opp-adj**, lam 60) | 0.2248 | 0.6412 |
| Stack (points + QB **opp-adj**, lam 150) | **0.2244** | **0.6404** |
| Stack (points + QB opp-adj + CPOE) | 0.2251 | (no lift — CPOE dropped) |

Opponent-adjusting and shrinking the passer signal beats **both** the points
baseline and the crude proxy, **monotonically in shrinkage** (a very noisy
per-QB signal that wants pulling hard toward the team level; qb_adj carries a
real learned weight). CPOE adds nothing on top. Real, but still **modest** and
still **backward-looking**.

**Not wired live yet — `epa_blend` stays 0 — for two honest reasons:**
1. **CLV, not Brier, is the production bar.** A backward-looking QB term helped
   calibration before but *hurt* CLV (the market prices QB form fast). The next
   gate is the closing-line backtest (NFL closing lines are on disk): the
   QB-adjusted margin must beat the close, not just the outcome.
2. **Live starter data is required and currently blocked.** Knowing each slate's
   projected starter needs a depth-chart feed (ESPN), which this environment's
   network policy blocks. Rolling per-QB EPA off the live PBP feed depends on it.

## Stage 1 — Wire EPA into projections (the #1 lever) 🔜
1. **EPA-backed team ratings for NFL** (M). Build a season EPA store from
   nflverse PBP (cache parquet locally; never re-pull). Blend EPA-derived
   strength into `models/generic.py` as a rating source or Elo prior. Validate:
   Brier/log-loss must improve on the 2019–2024 closing-line backtest before it
   goes live.
2. **EPA/PPA team ratings for NCAAF via CFBD** (M). Same, using `/ppa/teams`.
   Seed sparse early-season teams with **SP+** and shrink (ridge `lam` ↑ early).
3. **Backtest harness for EPA ratings** (S). Extend `backtest.py` to score the
   EPA variant head-to-head vs the points model (log-loss, Brier, ATS%, CLV).

## Stage 2 — NCAAF-specific priors (college is the harder sport) 🔜
4. **Preseason ratings from returning production + talent** (M). CFBD returning
   production is the strongest preseason signal; talent connects disjoint
   schedules. Replace cold-start league-average with these priors.
5. **FCS-game + neutral-site handling** (S). Collapse FCS opponents to a single
   replacement-level rating; flag neutral sites so HFA isn't applied.
6. **Tier-aware shrinkage** (M). A hierarchical/empirical-Bayes prior so elite
   and bottom teams aren't over-shrunk toward the mean (the SP+ approach).

## Stage 3 — Distribution & calibration polish 🔜
7. **Key-number-aware margin distribution** (M). Replace the plain Normal cover
   prob with an empirical / Gaussian-plus-spike mixture honoring NFL spikes at
   ±3, ±7. Most cover-probability value sits on the 3 and 7 half-points.
8. **Isotonic calibration of final win/cover/total probs** (S). Fit on a
   time-disjoint holdout; track ECE/reliability alongside the existing
   calibration-gap metric. (Devig, market-shrink, Kelly already in place.)
9. **CRPS in evaluation** (S). Add CRPS for the margin/total distributions to
   `backtest.py` (it already does Brier/log-loss/ATS/CLV).

## Stage 4 — QB & situational signals (NFL) ⏳
10. **Real-time QB adjustment** (L). The `qb_coeff` hook is intentionally 0: a
    backward-looking proxy helped calibration but hurt CLV (the market prices QB
    news fast). Needs a *live* starter/depth-chart + QB EPA+CPOE signal that can
    actually beat the close. High value, high effort, data-dependent.
11. **nfelo-style MOV multiplier** (S). Refine `models/elo.py`'s sqrt MOV to the
    autocorrelation-corrected form `ln(margin+1)·(2.2/(0.001·Δelo+2.2))`.
12. **Weather / rest / travel** (S–M). Rest is already wired (`rest_coeff`);
    add dome/wind/precip effects on totals via free weather (Open-Meteo present).

## Stage 5 — Optional model stacking ⏳
13. **Gradient-boosted tree layer** (L). Research: GBMs (XGBoost/LightGBM) beat
    plain regression and NNs for football. Stack a GBM on EPA + SP+/FPI + market
    features, then blend with the power rating and shrink to market. Only after
    Stages 1–3 prove the EPA inputs; adds a dependency, so gate on measured lift.

---

## Stage 6 — Baselines + tracking for sports we don't model yet 🔜
Leverage the paid FP/BP feeds and the market itself so we offer (and *track*) a
projection on every sport, then earn our keep by beating the baseline.
- ✅ **Market-implied baseline** — `project547/baseline.py`: de-vig the line
  (2-way or 3-way) into a baseline win prob; shown on every Other-Sports sheet.
  The de-vigged line is the strongest free baseline (our own research says so).
- ✅ **BettingPros baseline + validator** — `project547/bp.py`: de-vig BP's
  consensus moneyline into a baseline, surface BP's own prop projections/EV/
  recommended side as a second opinion, and a `baseline.compare` market-vs-BP
  agreement check. Shown on Other-Sports matchup sheets where BP covers the
  sport. FantasyPros only covers NFL/NBA/MLB, so it's for the modeled sports'
  player props, not the long tail.
- 🔜 **Our adjustment** — `baseline.apply_edge` is the hook; ships at 0 so we can
  **track the baseline itself from day one**, then add a little of our own math
  per sport and measure the lift.
- 🔜 **Tracking** — log each baseline (and adjusted) projection to the ledger so
  its CLV/accuracy is graded like any pick; this is how we prove value-add on
  sports we don't model.

## Data acquisition checklist (free unless noted)
- [ ] nflverse PBP parquet per season → local cache under `data/history/` (no key)
- [ ] CFBD free key in `.env` (`CFBD_API_KEY`); budget $10/mo Patreon tier before
      multi-season NCAAF backfills (1k→75k calls/mo)
- [ ] The Odds API closing lines: already the CLV backbone — keep frugal pulls
- [ ] Never re-pull paid data already on disk (see `docs/CONSOLIDATION.md`)

## How we'll know it worked
Win-probability **calibration first** (Brier/log-loss/ECE down on walk-forward),
then **CLV** over 300–500+ logged bets. Target ~52.5–54% ATS / positive CLV —
not 60%+. Anything claiming a >20% ROI backtest is a leakage red flag.
