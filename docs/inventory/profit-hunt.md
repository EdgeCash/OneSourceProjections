# Inventory: profit-hunt

**Source commit:** `a23ed560a0b22cfcdc1575b6c306b7fd56e57f44`
**Last activity:** 2026-06-12 (`edge-bot: "BP props harvest"`)
**Size:** 378 MB / ~12,000 files (235 MB is a single MLB-API HTTP cache; only ~6.6k LOC of Python)
**Inventoried:** 2026-06-24 (read-only)

> Product codename **SharperLabs** / "the math behind the bet." Private, paper-only research POC.
> **MLB-only in production.** NFL/NCAAF exist as *offseason planning + a config scaffold only* — no football model code, no football data.

---

## 1. Overview

A deterministic, auditable MLB sports-analytics engine. Core thesis: be an honest *estimator* (calibrated probabilities, not "Vegas-beater") and find edge as **CLV vs the opening line**. No ML, no black boxes — every number is hand-derived and unit-tested.

Two parallel codebases live in the repo:

| Tree | Role | Quality |
|---|---|---|
| `edge-nrfi-engine-v1/` (291 MB) | The live engine: harvest MLB Stats API → Log5/Poisson projections → 5 market gauges → report site + ledger. | **Production-quality** for a POC |
| `tenths/` (492 KB) | "The Tenths" — a cleaner nucleus salvaged from a prior build (`edge-equation-v1`); BYOE product + 12 reusable math modules + multi-sport config. | **Production-quality**, stdlib-only |

**Stack:** Python 3 (stdlib-first; `numpy`/`pandas`/`pybaseball`/`cairosvg`/`anthropic` optional and degrade gracefully). Static site generator (`site/build_site.py` → `app.html`), Cloudflare Worker (`worker/index.js`, `wrangler.jsonc`) for hosting.

**Entry points:**
- `edge-nrfi-engine-v1/main.py YYYY-MM-DD [--audit]` — daily NRFI table.
- `edge-nrfi-engine-v1/hub.py` — full research-hub pipeline (all 5 gauges, lineups, weather, report JSON).
- `edge-nrfi-engine-v1/tools/*.py` — backtests, harvesters, pick-sheet/DFS/settlement.
- `tenths/tools/byoe_backtest.py`, `tenths/tools/tenths_card.py` — BYOE backtest + daily Top-10.

**Docs are unusually good:** `docs/STRATEGY.md`, `docs/SALVAGE_MAP.md` (self-assessment of what to keep), `docs/football_plan.md` (NFL/NCAAF roadmap), `edge-nrfi-engine-v1/README.md`, `HUB.md`, `tenths/README.md`.

POC verdict: not throwaway. The math layer and CLV/calibration methodology are genuinely portable; the report rendering, harvesters, and committed slate data are throwaway.

---

## 2. Projection engine / models

### A. NRFI/YRFI inning engine (the flagship idea) — `edge-nrfi-engine-v1/src/engine/`
Bottom-up 1st-inning model, plate-appearance by plate-appearance.
- `log5.py` — Bill James Log5 odds-ratio + Tango multiplicative `ratio_matchup` for wOBA (`matchup_woba`, `matchup_obp`).
- `survival.py` — `compute_half_inning()` / `combine_game()`: Log5 wOBA → linear-weights run expectancy (mu) → park/weather factor → Poisson `e^-mu` scoreless prob → NRFI = P(top scoreless)·P(bottom scoreless). Includes `RollingCalibrator` (leakage-free trailing-window logit re-centering).
- I/O: in = ordered lineup wOBA/OBP (platoon-split) + pitcher 1st-inning splits + park factor; out = `GameResult` with full per-PA audit trail.
- Sport: **MLB**.
- **QUALITY 5/5.** Clever, defensible, fully auditable, well-tested. The bottom-up Log5→Poisson inning survival model is the salvageable idea.

### B. Full-game / First-5 run model — `src/engine/runmodel.py`
`team_lambda()` (xwOBA-ratio → expected runs, with `RUN_ELASTICITY`/`RUN_DISPERSION` NegBinom knobs and HFA) → `market_probs()` builds the exact joint score distribution by Poisson convolution and reads ML / run-line / total / First-5 straight off it. No simulation. **QUALITY 5/5** — closed-form, elegant, the right primitive.

### C. Market anchoring & EV core — `src/engine/market.py`
De-vig (`devig_two_way`, `consensus_fair` median across books), `blend()` (anchor model→de-vigged market), `ev_per_unit`, `best_book_price`, and a generic `two_way_value()` CLV/line-shop core. **QUALITY 5/5** — this is the reusable EV/edge methodology; pure math, no I/O.

### D. Staking & calibration — `src/engine/stake.py`, `calibration.py`, `grades.py`, `blend.py`
Fractional (quarter-) Kelly with caps; Brier-optimal shrink-toward-0.5 calibration fit from a 4,000+ pick ledger; gauge→letter-grade transform; time-weighted prior/current-season blending. **QUALITY 4-5/5.**

### E. The Tenths math modules — `tenths/math/` (12 modules, stdlib)
`ev.py`, `kelly_adaptive.py` (4-factor multiplicative shrink + daily cap), `isotonic.py` (PAV), `bayesian_shrinkage.py`, `ensemble.py` (Beta/Platt calibration + regime blending), `monte_carlo.py` (Bradley-Terry sampling-distribution of fair prob), `props.py` (NegBinom rate props), `scoring.py`, `decay.py`, `hfa.py`, `rho.py`, `stats.py`. **QUALITY 5/5** — the single most portable, sport-agnostic asset in the repo.

### F. BYOE ("Build Your Own Edge") — `tenths/byoe/`
`edge_scoring.py` (weighted + Poisson models, Kelly, grading, backtest), `edges.py` (Edge/EdgeStore/BoardStore/PickStore leaderboard), `leaderboard.py`, `walkforward.py`. User re-weights z-scored team stats; both the house and the user formula render on a dual-needle gauge. **QUALITY 4/5** — novel *product* concept; sound engineering. Salvageable if BestBets wants a user-facing edge sandbox.

### Research / EV-validation harnesses — `edge-nrfi-engine-v1/tools/research/`
- `edge_clv_backtest.py` — joins model probs to real de-vigged opening/closing lines; reports EDGE, ROI/units at best price, **CLV**, line-move-to-our-side. The honest profitability test.
- `poisson_glm.py` — dependency-free Poisson GLM (IRLS) that *fits* runmodel's hand-set constants; the principled upgrade path. **Worth porting.**
- `benchmark_models.py` — naive/current/GLM/market under expanding-window time-series CV.
- `edge_search_ensemble.py`, `edge_search_pit.py` (point-in-time, no look-ahead), `bp_projection_backtest.py`, `run_projection_metrics.py` (run-accuracy MAE/RMSE/bias/PIT/coverage).

---

## 3. Data assets / backtests

| Asset | Path | Size | Notes |
|---|---|---|---|
| MLB Stats API HTTP cache | `edge-nrfi-engine-v1/data/cache/` | 235 MB / 10.3k JSON | Free public API. Bulky, regenerable → **SKIP**. |
| Rendered report JSON | `data/reports/` | 38 MB / 1.3k files | Site artifacts → SKIP. |
| **BettingPros props/odds** | `data/research/bp_props_*.json`, `bp_odds.jsonl`, `bp_game_odds.jsonl` | ~13 MB | **CROWN JEWEL** — paid/partner-key BettingPros data (multi-book odds + props). Hard to re-derive. |
| **De-vigged closing lines** | `data/closing/mlb_closing_2026.jsonl` | 176 KB | **CROWN JEWEL** — built from a prior project; the CLV grading benchmark. |
| Graded pick ledger | `data/track/ledger.jsonl` | 1.2 MB | 4,000+ CLV-graded picks; feeds calibration. **Valuable for re-calibration.** |
| Booklines snapshots | `data/track/booklines/*.json` | ~0.7 MB ea | Multi-book open/close capture. |
| Pitcher / team season + PIT stats | `data/research/pitchers*.json`, `team_season_stats*.json` | ~0.6 MB | Point-in-time snapshots (no look-ahead) — reusable feature inputs. |
| Season ledger CSV | `edge-nrfi-engine-v1/Master_Season.csv` | 224 KB | Results. |
| Share graphics | `5-29-2026/`..`6-3-2026/` (top level) | ~29 MB PNG | Daily pick images (MLB + a few WNBA). Throwaway → SKIP. |

---

## 4. Integrations

All harvesters in `edge-nrfi-engine-v1/src/harvesters/`; key resolution via env vars (no secrets committed):

| Source | File | Auth / key |
|---|---|---|
| MLB Stats API (`statsapi.mlb.com`) | `schedule.py`, `stats.py`, `teams.py`, `gamelogs.py` | none (free); cache via `NRFI_CACHE_DIR`, `NRFI_DATA_MODE` |
| **BettingPros** (partner) | `bp.py`, `tools/research/bp_*_harvest.py` | `BP_PARTNER_KEY`, `BP_USER`, `BP_USER_KEY` |
| The Odds API | `oddsapi.py`, `gamelines.py` | API key env |
| ESPN | `espn.py` (`site.api.espn.com`) | none |
| FantasyPros | `fantasypros.py` | `FANTASYPROS_API_KEY` |
| Underdog | `underdog.py` (`underdogfantasy.com`) | `UNDERDOG_DISABLE` toggle |
| Baseball Savant | `savant.py` (via `pybaseball`) | none; degrades to MLB-API wOBA |
| Open-Meteo weather | `weather.py` (`archive-api.open-meteo.com`) | none |
| Anthropic (AI Lab curate) | `tools/ai_curate.py` | `ANTHROPIC_API_KEY` (optional, skips cleanly) |
| `tenths/adapters/bettingpros.py` | turns BP odds + ledger into the BYOE dataset | — |

---

## 5. Tests

Strong for a POC. **~412 test functions** total, stdlib-runnable (`python tests/test_engine.py` → "18 tests passed").
- `edge-nrfi-engine-v1/tests/` — 22 files, ~147 test funcs (engine, market, stake, harvesters, reports, settlement).
- `tenths/tests/` — 24 files, ~265 test funcs (every math module, BYOE schema/store, elo, composer, walk-forward).
- CI: `.github/workflows/*` (23 yml across repo).
Note: `pytest` not installed in this inventory env, but tests have stdlib `__main__` runners and pass.

---

## 6. Dependencies

`requests`, `pandas`, `numpy` (required); `pybaseball`, `cairosvg`, `anthropic`, `pytest` (optional, graceful-degrade). The engine + tenths math + EV core are **pure stdlib** — a deliberate, auditable design choice that makes porting easy.

---

## 7. Migration recommendation

| Component | Verdict | Reason |
|---|---|---|
| `tenths/math/` (12 modules) | **MIGRATE** | Sport-agnostic, stdlib, tested. Best reusable asset: Kelly-adaptive, isotonic, ensemble calibration, NegBinom props, Monte-Carlo fair-prob. |
| `src/engine/market.py` (de-vig, consensus, blend, EV, `two_way_value` CLV core) | **MIGRATE** | The reusable EV/edge methodology. Pure math, unit-tested. |
| `src/engine/runmodel.py` (lambda → Poisson-convolution joint dist → all markets) | **MIGRATE** | Elegant closed-form market primitive; directly mirrors the planned football margin/total model. |
| `src/engine/survival.py` + `log5.py` (NRFI inning engine) | **MIGRATE (MLB)** | Genuinely novel bottom-up Log5→Poisson inning survival idea. Port if BestBets keeps MLB NRFI. |
| `src/engine/stake.py` / `calibration.py` / `grades.py` | **MIGRATE** | Kelly sizing + Brier-shrink calibration + grading; sport-agnostic. |
| `tools/research/edge_clv_backtest.py`, `poisson_glm.py`, `benchmark_models.py`, PIT searches | **MIGRATE** | The honest CLV/ROI validation harness + the GLM fitter that frees runmodel's hand-set constants. **The novel "better technique."** |
| `tenths/byoe/` + `tenths/persistence/db.py` | **NEEDS-REVIEW** | Strong if BestBets wants a user edge-sandbox/leaderboard product; SKIP if not in scope. |
| `data/closing/mlb_closing_2026.jsonl`, `data/track/ledger.jsonl`, `data/research/bp_*` | **MIGRATE (data)** | Crown jewels: paid BettingPros data + de-vigged closing lines + 4k graded picks. Preserve before archiving. |
| `tenths/config/sport_config.py` (NFL/NCAAF Pythagorean/HFA/decay params) | **NEEDS-REVIEW** | Useful calibrated starting constants for football, but unvalidated (no model behind them yet). |
| `docs/football_plan.md` | **MIGRATE (doc)** | Concrete, sound NFL/NCAAF build plan reusing the shared spine — directly relevant to a football effort. |
| `src/harvesters/` | **NEEDS-REVIEW** | MLB-specific; the harvest *patterns* (http_cache, env-key resolution, graceful degrade) are worth copying, the MLB endpoints likely not. |
| `src/report/` (1.1k-line `render.py`), `site/`, `worker/`, `cloudflare/`, `brand/` | **SKIP** | Throwaway presentation layer tied to this product's branding/hosting. |
| `data/cache/` (235 MB), `data/reports/`, top-level date PNG folders | **SKIP** | Regenerable cache / rendered artifacts / share images; bulk of the 378 MB. |

**Football note:** There is *no* football model or data to migrate — only the planning doc and config constants. The MLB EV/CLV/calibration spine is explicitly designed (per `football_plan.md`, "~80% sport-agnostic") to be the base for NFL/NCAAF, so migrating the math + market + CLV-backtest layers *is* the highest-leverage football prep.
