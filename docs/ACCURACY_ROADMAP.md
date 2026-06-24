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
- ✅ **EPA ratings engine** — `onesource/epa.py`: opponent-adjusted EPA/play +
  success rate via ridge regression with team dummies; offense weighted 1.6×;
  garbage-time filter; EPA→points conversion. Pure, unit-tested.
- ✅ **nflverse PBP loader** — `onesource/clients/nflverse.py`: season
  play-by-play → opponent-adjusted team EPA (free, no key).
- ✅ **CFBD client** — `onesource/clients/cfbd.py`: NCAAF PPA(EPA), SP+/FPI,
  returning production, talent, lines, games (free key via `CFBD_API_KEY`).
- ✅ Research + inventory captured in `docs/`.

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
