# Inventory: Sports-projections (legacy "Edge Equation")

- **Source path:** `/home/user/Sports-projections`
- **Source commit:** `71a22fcd1f3f90a8e68560a3b4f4409287489bd9`
- **Last activity:** 2026-06-03 (automated odds/data commits by `edge-equation-bot`)
- **Total size:** 1.6 GB / ~5,300 files (4,550 JSON, 341 Python, 100 PNG, 83 JSONL)
- **Inventory date:** 2026-06-24 (read-only; nothing modified)

---

## 1. Overview

A sports betting projection + curation platform ("Edge Equation"). Computes player-prop and game-line
projections, converts them to no-vig fair probabilities, applies EV/Kelly math and discipline gates, and
surfaces "best bets" / "plays of the day." Includes a nightly AI-curation pipeline (4-persona Claude review)
and a Claude-vision extractor for competitor projection cards.

- **Stack:** Python 3 (engine is pure stdlib by design); FastAPI + Jinja2 + Tailwind/Alpine for the web app;
  APScheduler for in-process harvest cron; pandas/pyarrow/openpyxl for data + XLSX export; anthropic SDK for
  curation/vision.
- **Entry points:**
  - Web: `src/api/app.py` (FastAPI — "Build Your Own Edge", leaderboard, slate, backtest UI).
  - CLI harvest/backtest: `scripts/*.py` (run with `PYTHONPATH=. python3 scripts/<name>.py`).
  - `emergency_harvest.py` (root) — standalone harvest fallback.
- **Deploy targets:** Dockerfile + `fly.toml`, `render.yaml`, `railway.json`, `vercel.json`, `DEPLOY.md`.
  Data persisted on a mounted volume at `/app/data`.
- **Sports covered:** MLB (most mature), WNBA/NBA, **NFL & NCAAF** (football engine + props, validated on
  historical backfill; not yet wired to live odds at archive time).

---

## 2. Projection engine / models

Engine root: `src/engine/`. Sport-agnostic math lives in `src/engine/stats/` (poisson, bounds, weighting,
confidence) and `src/api/ev_math.py` (no-vig, Kelly, EV). Registry/runner: `src/engine/registry.py`,
`src/engine/runner.py`.

### Football (NFL + NCAAF) — primary focus

| Module | Function | Sport | I/O | Quality |
|---|---|---|---|---|
| `src/engine/football/model.py` | `FootballProjection`, power-rating game model | NFL, NCAAF | In: team game rows (points for/allowed, no-leakage `team_games_before`); Out: exp points, margin, total, win/cover/over probs. League priors (NFL margin_sd 13.5, NCAAF 16.5; HFA 2.0/2.7) | **5** — transparent, leakage-aware, league-parameterized, documented, backtested via `scripts/backtest_football_curation.py`; deliberately non-Poisson (correct for football). |
| `src/engine/football/elo.py` | Football Elo ratings | NFL, NCAAF | team results in → ratings out | **4** — tested (`test_football_elo.py`). |
| `src/engine/nfl_props/projection.py` | `project`, `over_probability`, `consensus_closing` | NFL | In: player prior-game stat lines + Odds API event payload; Out: (mu,sigma) + no-vig over prob | **5** — recency-weighted mean, empirical dispersion, Normal/Poisson per market, game-script `total_beta`; well-documented + multiple tests. |
| `src/engine/nfl_props/features.py`, `gamelines.py`, `params.py` | feature build / gamelines / tuned params | NFL | — | **4** — each has a dedicated test. |
| `src/engine/football_features/{harvest,matchup,store}.py` | ESPN box-score harvest + historical store | NFL (NCAAF-ready) | ESPN free JSON → FootballGameRow/PlayerGameRow | **4** — shared row shape with CSV ingest; network-mocked tests. |

### Other sports (context)

- **MLB:** richest — `engine/games/` (moneyline, run_line, total, NRFI/YRFI, first-five), `engine/props/`
  (12 batter/pitcher prop models), `engine/savant/`, `statcast_xstats.py`, park/umpire/weather features.
  Quality **4-5**, heavily tested.
- **WNBA/NBA:** `engine/wnba_games/`, `engine/wnba_props/`, `engine/wnba_features/`. Quality **4**.
- **Calibration:** `engine/calibration/` (closing_lines, game_calibration, props_calibration, metrics, report).
- **Curation/discipline gates:** `engine/curation/` (candidate, gates, parlay, policy) — the EV discipline layer.

---

## 3. Data assets / backtests — THE BIG ONE (1.4 GB in `data/`)

| Directory | Size | Format | What it is | Provenance |
|---|---|---|---|---|
| **`data/raw/rotowire/`** | **907 MB** | JSON (per-date) | **CROWN JEWEL.** Rotowire feeds: `players/` (406M), `daily_projections/` (339M — per-game projected batting/pitching lines), `depth_chart/` (53M), `injuries/` (49M), `news/` (29M), lineups/expected/projected. | **PAID API** (`ROTOWIRE_API_KEY`, `api.rotowire.com`). **Never regenerate.** |
| `data/backfill/` | 235 MB | JSON + JSONL | Per-season historical box scores. `mlb/` 121M, `wnba/` 64M, **`ncaaf/` 43M (2004-2025!)**, `nba/` 5.2M, **`nfl/` 3.0M (2025)**. | NCAAF from committed CSV set (`_source/cfb-*.csv.gz`, college-football-data style) via `scripts/ncaaf_ingest_csv.py`; NFL/WNBA from free ESPN. **Migrate (valuable history).** |
| `data/vendor_cards/bettorsheets/` | 135 MB | PNG + TXT | Competitor projection-card images + extracted reports, captured from YouTube. Decoded via Claude-vision (`src/api/vendor_cards/`). | Manual/scraped capture. High effort to recreate. **Needs-review (migrate if vendor analysis continues).** |
| `data/backtest/` | 76 MB | CSV + JSON | **Backtest result files:** `games_detail.csv`, `games_summary.csv`, `props_detail.csv`, `props_summary.csv` (cols: roi_pct, hit_rate, edge, calibration_gap...), `wnba_2025_metrics.json`. | Derived output. **Migrate (results) / regenerable.** |
| `data/closing_lines/mlb/2026.jsonl` | 26 MB | JSONL | No-vig closing-line snapshots (captured_at, market, side, fair prob) — sharp grading reference. | From Odds API harvest. **Migrate (point-in-time, not re-fetchable).** |
| `data/backtests/` | 3.9 MB | CSV | WNBA props calibration backtest runs (timestamped). | Derived. Regenerable. |
| `data/raw/odds_api_historical/` | 17 MB | JSON | Historical odds by date/sport (incl. `americanfootball_nfl/`, mlb, wnba), 2023-2026. | **PAID** (Odds API historical endpoint — costs credits). **Migrate.** |
| `data/raw/optic_odds/` | 16 MB | JSON | OpticOdds fixtures/odds. | **PAID** (`OPTIC_ODDS_API_KEY`). **Migrate.** |
| `data/raw/the_rundown/` | 3.8 MB | JSON | The Rundown odds/lines. | **PAID** (`THE_RUNDOWN_API_KEY`). **Migrate.** |
| `data/raw/odds_api/`, `mlb_statsapi/`, `weather/` | ~2.3 MB | JSON | Live Odds API snapshots, MLB StatsAPI, weather. | Mixed paid/free. |
| `data/{season_stats,calibration,results,statcast,retrosheet_names,curation,curated,models}/` | <1 MB each | JSON/CSV | Fitted calibrators, season stats, curation outputs, `models/` (NFL model artifacts, `NFL_MODELS_DIR`). | Derived. **Migrate calibration + models.** |
| `briefs/` (repo root) | 2.7 MB | XLSX/HTML/JSON/TXT | Per-date daily slate briefs, engine candidates, Claude prompts, edge-equation HTML reports (2026-05-28..06-03). | Output. Migrate selectively. |

**Paid-API-derived "crown jewels" (never regenerate):** `data/raw/rotowire/` (907M), `data/raw/odds_api_historical/`,
`data/raw/optic_odds/`, `data/raw/the_rundown/`, `data/closing_lines/`.

---

## 4. Integrations

API clients with thin one-function-per-endpoint design, keys from env:

| Integration | Client path | Key env var |
|---|---|---|
| The Odds API | `src/engine/odds_api/client.py` (+ `harvest.py`, `store.py`) | `ODDS_API_KEY` (sport keys incl. `americanfootball_nfl`, `americanfootball_ncaaf`) |
| The Rundown | `src/engine/the_rundown/client.py` | `THE_RUNDOWN_API_KEY` |
| MLB StatsAPI | `src/engine/mlb_statsapi/client.py` | (free) |
| Baseball Savant | `src/engine/savant/client.py` | (free) |
| MLB Weather | `src/engine/mlb_weather/client.py` | `WEATHER_API_KEY` |
| Rotowire | (harvest scripts; `scripts/check_api_endpoints.py`) | `ROTOWIRE_API_KEY`, `ROTOWIRE_BASE_URL` |
| OpticOdds | (raw under `data/raw/optic_odds/`) | `OPTIC_ODDS_API_KEY`, `OPTIC_ODDS_BASE_URL` |
| ESPN (football/basketball box scores) | `*_features/harvest.py` | (free, no key) |
| Anthropic (curation + vendor-card vision) | `src/api/curation.py`, `src/api/vendor_cards/` | `ANTHROPIC_API_KEY`, `VENDOR_CARD_MODEL` |
| Apify (PrizePicks) | `src/api/prizepicks/apify_source.py` | `APIFY_TOKEN` |
| Resend (email export) | `src/api/email_sender.py` | `RESEND_API_KEY`, `EXPORT_EMAIL_*` |

Key resolution: all via `os.getenv` / `os.environ`. Data-dir overrides: `DATA_DIR`, `BACKFILL_DATA_DIR`,
`CLOSING_LINES_DIR`, `ODDS_API_DATA_DIR`, `THE_RUNDOWN_DATA_DIR`, `NFL_MODELS_DIR`, `BYOE_DB_PATH`.

---

## 5. Tests

- **Framework:** pytest (+ httpx); `tests/conftest.py`. **89 test files** in `tests/`.
- **Football coverage (good):** `test_football_model.py`, `test_football_elo.py`, `test_football_harvest.py`,
  `test_football_store_matchup.py`, `test_ncaaf_ingest.py`, `test_nfl_props_projection.py`,
  `test_nfl_props_features.py`, `test_nfl_props_gamelines.py`, `test_nfl_props_params.py`,
  `test_nfl_historical_props_harvest.py`.
- Broad coverage of MLB, WNBA, calibration, curation, EV/best-bets, web views; network calls mocked
  (CI egress blocks ESPN/vendor endpoints).

---

## 6. Dependencies (`requirements.txt`)

Engine = stdlib only. Support libs: `requests`, `pandas>=2.0`, `pyarrow>=14`, `fastapi`, `uvicorn`,
`jinja2`, `python-multipart`, `itsdangerous`, `anthropic>=0.50`, `pydantic>=2`, `pillow`, `apscheduler`,
`openpyxl`, `pytest`, `httpx`. External binaries (not pip): `yt-dlp`, `ffmpeg` (vendor-card capture).

---

## 7. Migration recommendation

Source commit: `71a22fcd1f3f90a8e68560a3b4f4409287489bd9`

### MIGRATE (valuable, hard/impossible to regenerate)

- **`data/raw/rotowire/` (907M)** — PAID, irreplaceable historical feed. Top priority.
- **`data/raw/odds_api_historical/`, `data/raw/optic_odds/`, `data/raw/the_rundown/`** — PAID, credit-costing.
- **`data/closing_lines/` (26M)** — point-in-time sharp lines, not re-fetchable.
- **`data/backfill/ncaaf/` (43M, 2004-2025) + `data/backfill/nfl/`** — football history; the focus assets.
- **`data/backfill/mlb,wnba,nba/`** — historical box scores (cheap re-derive but large; migrate as-is).
- **`data/backtest/` results, `data/calibration/`, `data/models/`** — fitted artifacts + proven results.
- **Football engine code** — `src/engine/football/`, `src/engine/nfl_props/`, `src/engine/football_features/`
  (quality 4-5, tested, leakage-aware). Plus shared `src/engine/stats/`, `src/api/ev_math.py`,
  `src/engine/curation/`, `src/engine/calibration/`.
- **`data/vendor_cards/` (135M)** — keep if competitor-card analysis continues (NEEDS-REVIEW otherwise).

### NEEDS-REVIEW

- **Web app (`src/api/app.py` + templates)** — large surface; migrate only if the UI is part of the new system.
- **`scripts/` (688K, ~40 scripts)** — many are sport/era-specific harvest/backtest one-offs; cherry-pick the
  football + calibration ones (`nfl_*`, `ncaaf_ingest_csv.py`, `backtest_football_curation.py`,
  `fit_football_calibration.py`).
- **`data/backtests/` (WNBA calibration runs)** — regenerable derived output; migrate only if needed.

### SKIP (dead/duplicate/regenerable code or low-value)

- **`emergency_harvest.py`** (root) — ad-hoc fallback, superseded by `scripts/` harvesters.
- Deploy configs for unused platforms (`vercel.json`, `render.yaml`, `railway.json` — keep one).
- `briefs/` daily HTML/XLSX reports — disposable outputs (keep a sample if desired).
- `public/`, `graded/`, `picks/` — small live-app state, not needed for a fresh build.
- `.git/` history is not required for the data assets.
