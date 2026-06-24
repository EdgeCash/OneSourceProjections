# Inventory: edge-equation-v1 (ORIGINAL engine)

- **Source path:** `/home/user/edge-equation-v1`
- **Source commit:** `04ae8944a7482a881980633194130a2323724f7d`
- **Last activity:** 2026-05-26 (data harvests / closing-line snapshots; code/engine work tails off earlier — football skeletons authored before this).
- **Scope note:** Migration target is the **Python engine only**. This repo also ships a Vercel/Next.js frontend (`web/`, `website/`, `api/`, `.tsx`/`.ts` files) and content/posting automation — those are **out of scope** and flagged SKIP below.

---

## 1. Overview

Edge Equation v1 is a deterministic, multi-sport projection + edge-betting engine. It projects markets (spreads, totals, ML, player props, NRFI), computes vig-adjusted edges, tiers picks (ELITE/STRONG/MODERATE/LEAN/NO_PLAY), builds parlays, settles ledgers, and publishes daily cards/emails plus a website feed.

- **Stack:** Python ≥3.10. Core deps: `pydantic>=2.7`, `pandas`, `numpy`, `httpx`, `python-dotenv`, `openpyxl`, `requests`. Optional extras: `[api]` FastAPI/uvicorn, `[parlay-lab]` PuLP, `[polycast]` google-genai/tenacity/pillow/cairosvg, `[nrfi]` ML/data stack.
- **Engine package:** `src/edge_equation/` (842 `.py` files total in repo).
- **Entry points (engine):** top-level `run_daily_all.py`, `run_daily_mlb.py`, `run_daily_nfl.py`, `run_daily_ncaaf.py`, `run_daily_wnba.py`, `run_edge_report.py`, `run_roast_report.py`; per-engine `run_daily.py` / `daily.py`; `python -m edge_equation` (`src/edge_equation/__main__.py`). `Makefile` orchestrates targets.
- **Frontend/non-engine (SKIP):** `web/`, `website/`, `api/` (FastAPI deploy + 96 `.tsx`), `polycast/` + `src/polycast/` (Gemini social automation), `deployment/`, `railway.json`, `render.yaml`, `src/edge_equation/posting/`, `content_gen/`, `publishing/`, `roast_report/`, `auth/`, `compliance/`, `premium/`.

---

## 2. Projection engine / models

Repo has THREE generations of projection code: (a) legacy `src/edge_equation/engine/` + `src/edge_equation/models/`, (b) the mature per-engine packages under `src/edge_equation/engines/` (MLB-centric), and (c) the football engines (NFL/NCAAF) which are **architecturally complete skeletons with real feature/data code but no wired projection model yet**.

### Football core (shared NFL+NCAAF) — `src/edge_equation/engines/football_core/`
Shared vocabulary + data loaders for both football leagues. **Real, substantial code (~2,500 lines under `data/`).**
- `markets.py`, `weather.py`, `rest_days.py`, `qb_adjustments.py` — shared market vocab, outdoor venue/weather impact scoring, rest-day classifier, QB-injury → expected-points delta (highest-leverage football feature).
- `data/nflverse_loader.py` (196) — pulls nflverse public parquet (schedule, PBP w/ EPA/win-prob); **no API key**.
- `data/cfbd_loader.py` (316) — College Football Data API (`/games`, `/plays`, `/lines`); key `CFBD_API_KEY`.
- `data/backfill_nfl.py` (431), `data/backfill_ncaaf.py` (477) — orchestrate multi-season backfill with checkpointing.
- `data/odds_history.py`, `weather_history.py`, `storage.py`, `diagnostics.py`, `checkpoints.py`.
- Inputs: nflverse parquet / CFBD JSON / odds history. Outputs: normalized DataFrames into storage layer.
- **QUALITY: 5** — clean, documented, the canonical football data foundation; directly reusable.

### NFL engine — `src/edge_equation/engines/nfl/`
- Sport: NFL. **Status: Phase F-1 skeleton.** `daily.py` `build_nfl_card` returns an empty card ("engine not ready"); `models/` empty; `output/payload.py` is a shell (`engine="nfl_skeleton"`); `source/`, `ledger.py` are DDL/stubs.
- **BUT real feature code exists:** `features/team_elo.py` (199 — FiveThirtyEight-style Elo, K=20, HFA=55, rest/bye adjustments), `features/composites.py` (244 — QB×defense, weather, rest×HFA composites), `features/tracking.py` (280). `config.py` (NFLConfig + ProjectionKnobs), `markets.py` (Odds API key mapping for spreads/totals/ML + full prop set), `thresholds.py`, `backtest_cli.py`, `game_results_parlay.py`, `player_props_parlay.py`, `parlay_runner.py`.
- Inputs: nflverse/odds; Outputs: (intended) NFLOutput card. **Projection model is NOT implemented.**
- **QUALITY: 3** — excellent architecture + real, reusable feature builders (Elo/composites are the keepers), but no working projection/edge pipeline.

### NCAAF engine — `src/edge_equation/engines/ncaaf/`
- Sport: NCAAF/CFB. **Status: skeleton**, mirrors NFL. `features/composites.py` (171) is the main real piece; rest (`daily.py`, `models/`, `source/`, `output/payload.py`) are stubs/DDL. `config.py`, `markets.py`, `thresholds.py`, parlay modules present.
- **QUALITY: 2** — thinner than NFL; structure + composites only, no projection model.

### Full-game engine (MLB) — `src/edge_equation/engines/full_game/`
- Sport: MLB. **Mature, production.** `projection.py` (315) — Poisson/Skellam per-team projection for Game Total, F5 Total, Run Line/Spread, ML, Team Total with Bayesian shrinkage + HFA. `daily.py` (472), `edge.py`, `explain.py`, `ledger.py` (539), `odds_fetcher.py` (408), `markets.py`, `config.py`.
- **QUALITY: 5** — canonical, well-tested full-game implementation; the pattern NFL/NCAAF were meant to copy.

### Player props engine (MLB) — `src/edge_equation/engines/player_props/`
- Sport: MLB. **Mature.** `projection.py` (295), `daily.py` (569), `edge.py`, `gates.py`, `ledger.py` (455), `explain.py`, `odds_fetcher.py`.
- **QUALITY: 5** — canonical props pattern.

### NRFI engine — `src/edge_equation/engines/nrfi/`
- Sport: MLB (No-Runs-First-Inning). **Most mature ML engine** (61 `.py`, 676K). `models/` has `poisson_baseline.py`, `inference.py`, `model_training.py`, `calibration.py`, `calibration_alternatives.py`; plus `features/`, `simulation/`, `training/`, `backtest_historical.py`, `dashboard.py`, `email_report.py`.
- **QUALITY: 5** — deepest, best-tested engine in the repo (heaviest test coverage). Not football, but the gold-standard reference implementation.

### Other sport engines — `src/edge_equation/engines/{mlb,nba,nhl,wnba,soccer,kbo,npb,ncaab_men,ncaab_women,ncaa_baseball,ncaa_softball}/`
- Varying maturity (most are `run_daily.py` runners). MLB + WNBA most developed; others lightweight. NEEDS-REVIEW per sport; out of NFL/NCAAF focus.

### Shared / cross-cutting
- `engines/parlay/` — builder, correlations, strategies, strategy_resolver (real). **QUALITY: 4.**
- `engines/_common/` — sport_runner, games_only, placeholder_slates. **QUALITY: 4.**
- `engines/tiering.py` — engine-wide edge-ladder tier classifier. **QUALITY: 4** (reused everywhere).
- `engines/football_core/` already covered.

### Legacy projection code (likely superseded)
- `src/edge_equation/engine/` (betting_engine, pipeline, feature_builder, slate_runner, engine_registry, realization, major_variance, modes...) and `src/edge_equation/models/` (`moneyline_engine.py`, `nrfi_engine.py`, `props_engine.py`, `wnba_props_engine.py`, `registry.py`, `demo_model.py`).
- **QUALITY: 2** — older generation predating the `engines/` packages; likely duplicate/dead. NEEDS-REVIEW (do not migrate blindly).

---

## 3. Data assets / backtests (crown jewels)

Total `data/` = **330M**.

- **`data/backfill/` (282M)** — historical season datasets, the core asset:
  - `nfl/` **38M** — `2021-2026` `schedule.json`, `weekly_player.json`, `2024/games.jsonl`+`player_weekly.jsonl`. **nflverse-derived (free, no paid API).** MIGRATE.
  - `ncaaf/` **29M** — `2021-2025` `games.jsonl` + `player_games.jsonl` (~8MB/yr). **CFBD-derived (free tier).** MIGRATE.
  - mlb 107M, nba 50M, nhl 26M, wnba 23M, plus ncaa_baseball/soccer/ncaab/npb/kbo/ncaa_softball.
- **`data/closing_lines/` (45M)** — `mlb/nba/nhl/wnba` only (no NFL/NCAAF). **PAID-API-DERIVED (The Odds API closing snapshots) — crown jewel, hard to reconstruct.** MIGRATE (MLB/etc.), but note football not present.
- `data/season_stats/` (800K), `data/results/` (304K→`full_game`), `data/statcast/` (256K, MLB), `data/rotowire/` (1.7M, scraped), `data/retrosheet_names/`, `data/picks_log/`, `data/man_picks/`, `data/comparison/`.
- Loose CSVs: `MLB_2026_YTD_*`, `WNBA_2026_YTD_*`, `Sports_2026_YTD_Historical_Props.csv`.
- **404 `.xlsx`** files (exporter outputs) scattered — likely regenerable, SKIP.
- Backtest code: `engines/nrfi/backtest_historical.py`, `engines/wnba/backtest_historical.py`, per-engine `backtest_cli.py` (incl. `nfl/`, `ncaaf/`), `football_core/backtest_cli_common.py`, `src/edge_equation/backtest/`.

---

## 4. Integrations (API clients)

- **The Odds API** — primary odds source. Client: `src/edge_equation/ingestion/odds_api_client.py` (`TheOddsApiClient`, cache-first via `persistence/odds_cache.py`, base `https://api.the-odds-api.com/v4/sports`). **Key: env `THE_ODDS_API_KEY`** (or explicit `api_key=`). Also consumed by `ingestion/odds_api_source.py`, `engines/*/odds_fetcher.py`, `engines/nrfi/data/odds.py`, `exporters/closing_lines/`, `data_fetcher.py`. **QUALITY: 5**, canonical odds client.
- **College Football Data API** — `engines/football_core/data/cfbd_loader.py`. **Key: env `CFBD_API_KEY`** (free tier). NCAAF backfill source.
- **nflverse** — `engines/football_core/data/nflverse_loader.py`. Public parquet, **no key**. NFL backfill source.
- **MLB StatsAPI** — `src/edge_equation/scrapers/mlb/` via `requests` (statsapi.mlb.com), no key.
- **Other ingestion sources:** `ingestion/{nfl,nba,nhl,mlb,wnba,soccer}_source.py`, `source_factory.py`, `normalizer.py`, `manual_csv_source.py`.
- **Gemini (Polycast)** — `src/polycast/` (out of scope, frontend/social).
- Key resolution everywhere: `os.environ` via python-dotenv; see `.env.example` (5.4K).

---

## 5. Tests

- **Framework:** pytest (`pytest>=8.0`); config in `pyproject.toml` `[tool.pytest.ini_options]`. Separate `tests_api/` for FastAPI.
- **Coverage:** **228 test files** in `tests/`. NRFI is by far the most covered (~30 `test_nrfi_*`). Full-game well covered (`test_full_game_*` ×9). Player props: `test_player_prop_projections.py`.
- **Football:** `test_checkpoint4_football.py`, `test_football_backfill.py`, `test_football_parlay_engines.py`, `test_football_skeletons.py` — confirm football is tested at the **skeleton/feature/backfill** level, not full projection.
- **Run:** `python -m pytest` (Makefile `test` target only runs one file — `tests/test_daily_sheet_generator.py`). Tests use injected `httpx.MockTransport` — no live network.

---

## 6. Dependencies (key libs)

Core: `pydantic>=2.7`, `pandas>=2.2`, `numpy>=1.26`, `httpx>=0.27`, `python-dotenv`, `openpyxl`, `requests`.
Dev: `pytest`, `ruff`, `mypy`, `fastapi`, `uvicorn`.
Optional: `pulp` (parlay ILP), `google-genai`/`tenacity`/`pillow`/`cairosvg` (polycast), NRFI ML extras (`[nrfi]`).
No heavy ML framework in core — projection is deterministic (Poisson/Skellam/Elo); NRFI carries the optional ML stack.

---

## 7. Migration recommendation (per component)

| Component | Action | Reason |
|---|---|---|
| `engines/football_core/` (markets, weather, rest_days, qb_adjustments) | **MIGRATE** | Canonical shared football vocab/features; reusable as-is. |
| `engines/football_core/data/` (nflverse_loader, cfbd_loader, backfill_*) | **MIGRATE** | Real, working free-API loaders for NFL+NCAAF history — foundation. |
| `engines/nfl/features/` (team_elo, composites, tracking) | **MIGRATE** | Best NFL feature implementations in the repo; production-ready. |
| `engines/nfl/` (config, markets, thresholds, parlay modules) | **MIGRATE** | Solid architecture + Odds API mapping; keep as scaffold. |
| `engines/nfl/{daily,models,output,source,ledger}` projection | **NEEDS-REVIEW** | Skeleton/stub — no working projection; port pattern from `full_game`, then build. |
| `engines/ncaaf/` | **NEEDS-REVIEW** | Skeleton + composites only; thinner than NFL. Migrate structure, build projection. |
| `engines/full_game/` (MLB) | **MIGRATE** | Canonical full-game projection (Poisson/Skellam) — the reference pattern for NFL/NCAAF. |
| `engines/player_props/` (MLB) | **MIGRATE** | Canonical props pattern; reference for football props. |
| `engines/nrfi/` (MLB) | **MIGRATE** | Gold-standard ML engine + best tests; reference implementation (not football). |
| `engines/parlay/`, `engines/_common/`, `engines/tiering.py` | **MIGRATE** | Shared, reused, working. |
| `ingestion/odds_api_client.py` + `persistence/odds_cache.py` | **MIGRATE** | Canonical Odds API client (cache-first); key via `THE_ODDS_API_KEY`. |
| `data/backfill/nfl/` (38M), `data/backfill/ncaaf/` (29M) | **MIGRATE** | Crown-jewel historical datasets, free-API-derived, multi-season. |
| `data/closing_lines/` (45M) | **MIGRATE** | Paid-API-derived (The Odds API) — irreplaceable; note: no NFL/NCAAF in it. |
| Other-sport backfill data (mlb/nba/nhl/wnba...) | **NEEDS-REVIEW** | Valuable but outside NFL/NCAAF focus; migrate selectively. |
| `src/edge_equation/engine/` + `src/edge_equation/models/` (legacy) | **NEEDS-REVIEW** | Older generation, likely superseded/dupe of `engines/`; verify before dropping. |
| Other sport engines (nba/nhl/soccer/kbo/npb/ncaab/ncaa_*) | **NEEDS-REVIEW** | Out of focus; varying maturity. |
| `*.xlsx` exporter outputs (404 files), `exporters/` | **SKIP** | Regenerable artifacts / output formatting. |
| `web/`, `website/`, `api/`, `*.tsx`, `deployment/`, `railway.json`, `render.yaml` | **SKIP** | Frontend / Vercel site / deploy config — out of scope (Python engine only). |
| `polycast/`, `src/polycast/`, `posting/`, `content_gen/`, `publishing/`, `roast_report/`, `auth/`, `compliance/`, `premium/` | **SKIP** | Social/content/auth/billing automation — not the projection engine. |

### Canonical "keep" set for NFL/NCAAF migration
1. `engines/football_core/` (incl. `data/` loaders) — the football foundation.
2. `engines/nfl/features/` (team_elo + composites) — best football feature code.
3. `engines/full_game/` + `engines/player_props/` (MLB) — the working projection/edge patterns to clone for football.
4. `engines/nrfi/` — gold-standard reference (architecture, calibration, tests).
5. `ingestion/odds_api_client.py` + `persistence/odds_cache.py` — odds integration.
6. `data/backfill/{nfl,ncaaf}/` + `data/closing_lines/` — datasets.

> **Key caveat:** NFL & NCAAF have excellent *scaffolding, features, and data* but **no implemented projection/edge model** (Phase F-1 skeletons). The migration must port the projection logic from `full_game`/`player_props`/`nrfi` to complete them.
