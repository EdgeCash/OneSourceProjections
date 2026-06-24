# Research synthesis — what accurate football models do vs. what we have

This synthesizes the three research reports in this folder against OneSource's
actual engine (audited file-by-file, June 2026), to answer the question that
kicked off the consolidation: *what do the most accurate NFL/NCAAF projection
models do, and what do we need to do to match them with the resources we have?*

Sources: `01-sota-methodology.md`, `02-data-and-features.md`,
`03-modeling-calibration-evaluation.md`. Read those for citations/URLs.

## The one-paragraph answer

The best public football models are a **layered ensemble**: an opponent-adjusted
**EPA/play** power rating as the backbone, optionally stacked with gradient-boosted
trees, then **shrunk toward the de-vigged market line**, with outputs **calibrated**
(isotonic/Platt) and validated **walk-forward** on proper scoring rules with
**CLV** as the business metric. OneSource already has the *betting* half of this
right (devig, market-shrink, fractional Kelly, CLV tracking, calibrated EV bands,
walk-forward backtest). What it lacks is the *ratings* half: it rates teams on
**points scored/allowed**, not EPA — the single biggest accuracy lever — and it
has **no NCAAF advanced-data source**. Closing those two gaps is the highest-value
work, and both are achievable with free data (nflverse, CollegeFootballData).

## Gap analysis (research finding → our state → verdict)

| Area | What accurate models do | OneSource today | Gap |
|---|---|---|---|
| **Team rating signal** | Opponent-adjusted **EPA/play** + success rate, garbage-time filtered, offense weighted ~1.6× defense | Points scored/allowed, shrunk to league avg, optional SoS (`models/generic.py`) | **LARGE — #1 lever.** Points are downstream of EPA and noisier |
| **Power-rating core** | Elo w/ MOV multiplier, modeled HFA, season regression | Elo w/ MOV (sqrt) + season regress + per-sport HFA already present (`models/elo.py`, `sports.py`) | Small — refine MOV to nfelo autocorrelation form |
| **QB adjustment (NFL)** | Rolling QB EPA+CPOE shifts team strength on injury/change — highest-value NFL upgrade | `qb_coeff` hook exists but =0 (measured: backward-looking proxy hurts CLV) | Medium — needs a *real-time* QB/depth signal, not a backward proxy |
| **NCAAF data** | CFBD: PPA(EPA), SP+/FPI, returning production, talent/portal, lines | None — NCAAF runs on ESPN scores only | **LARGE — required for college accuracy** |
| **NFL play data** | nflverse PBP (EPA/WP/CPOE), free, back to 1999 | Ingests nflverse *game results* only (`nfl_history.py`) | Medium — add the PBP/EPA layer |
| **Distribution & key numbers** | Margin distribution w/ spikes at ±3, ±7 (not plain Gaussian) | Normal margin/total model | Medium — key-number-aware margin distribution |
| **Devig** | Multiplicative / power / Shin on a sharp book | Present (`odds.py`, `calculators.py`) | Covered |
| **Market shrink** | Blend small model weight w/ market prior | `MARKET_SHRINK=0.5` (`config.py`, `odds.py`) | Covered (could tune lower per research's w≈0.1–0.3) |
| **Calibration** | Isotonic/Platt on time-disjoint holdout; reliability/ECE | Calibration gap tracked in backtest/scorecard | Medium — add explicit isotonic calibration of final probs |
| **Staking** | Fractional Kelly (0.25–0.5) | `KELLY_FRACTION=0.25` | Covered |
| **Evaluation** | Walk-forward, Brier/log-loss/CRPS, ATS% vs 52.4%, CLV | Walk-forward backtest, calibration, CLV (`backtest.py`, `clv.py`) | Small — add CRPS for margin distributions |
| **Leakage discipline** | Pre-kickoff features only; recompute SoS on pre-game data | Football model is explicitly leakage-aware (`team_games_before`) | Covered |

## What this means

Two big gaps, both data-driven, both fixable with **free** sources:

1. **EPA ratings.** Rate teams on opponent-adjusted EPA/play instead of (or
   blended with) points. Shipped in this consolidation as a foundation:
   `onesource/epa.py` (ridge opponent-adjustment), `clients/nflverse.py`
   (NFL PBP → EPA), `clients/cfbd.py` (NCAAF PPA/EPA + priors). Not yet wired
   into live projections — staged in the roadmap so it's *validated* against the
   backtest first (the same "prove the edge before wiring" discipline the
   football model already follows).

2. **NCAAF advanced data.** CFBD is the unlock: PPA (their EPA), SP+/FPI priors,
   returning production, and talent to connect a disjoint schedule. Free tier is
   1k calls/mo — fine for weekly in-season pulls; budget the $10/mo Patreon tier
   before doing multi-season backfills.

The betting/edge machinery is already sound. The accuracy upside is almost
entirely in the **inputs** (EPA, college advanced stats), not the bet math.

## Realistic target

Research is blunt: the bar is the market, and realistic edges are **~52.5–54%
ATS / positive CLV**, not 60%+. Success = beating the close, measured by CLV over
300–500+ bets — which OneSource already tracks. The EPA upgrade should show up
first as **better win-probability calibration** (lower Brier/log-loss) and then
as CLV, validated walk-forward before going live.
