# Inventory: OneSourceProjections

> Repo: `/home/user/OneSourceProjections` (read-only inventory)
> Source commit: `d2897f20ca8e48a7ef21923bd5da7933a2a39944`
> Branch: `claude/bestbets-consolidation-projection-research-7otk07`
> Last activity: 2026-06-24 16:18Z (automated `projections-bot data: hourly update`)
> Inventoried: 2026-06-24

This is the **newest active Python deterministic model** and the recommended consolidation base. It already absorbed historical data from the prior EdgeCash repos (Sports-projections, edge-equation-v1, Sports-stats-data, profit-hunt) on 2026-06-12.

---

## 1. Overview

- **What it does:** Multi-sport betting model (MLB, WNBA, NBA, NFL, NCAAF, NHL). Projects game markets (moneyline / total / spread) and player props, computes edges against market lines (BettingPros consensus + The Odds API multi-book), and serves a password-gated Streamlit dashboard. Forward-tests itself via an hourly GitHub Action that snapshots odds, archives projections, and grades finished games.
- **Language/stack:** Python 3 (uses 3.10+ syntax: `X | Y` unions, `zoneinfo`). pandas / numpy / scipy for modeling, Monte Carlo (Poisson) for low-scoring sports, Normal/Elo for others. Streamlit + Altair dashboard. Optional Anthropic SDK for in-app AI analyst.
- **Entry points:**
  - `scripts/run_daily.py` — run pipeline, write `data/output/latest.json` (`--sports WNBA,MLB` to filter).
  - `scripts/hourly_update.py` — the production cadence (snapshot + project + grade + write). Driven by `.github/workflows/hourly.yml`.
  - `scripts/rebuild_site.py` — re-run pipeline with paid APIs **replayed from the committed library** (zero credits) via `onesource/replay.py`.
  - `scripts/run_backtest.py` — walk-forward backtests → `reports/`.
  - `app/dashboard.py` — `streamlit run app/dashboard.py`.
  - Helper scripts: `compute_park_factors.py`, `discover_markets.py`, `dump_bp_props.py`, `make_preview.py`, `notify_test.py`.
- **Repo size:** ~112 MB total (~44 MB `.git`, ~66 MB working tree, of which ~59 MB is `data/history`).
- **CI:** 4 workflows — `hourly.yml`, `rebuild.yml`, `confirm-bp-books.yml`, `notify-test.yml`.

---

## 2. Projection engine / models

Core model code lives in `onesource/models/`; the orchestration and market math in `onesource/pipeline.py`.

| Module | What it does | Sports | Inputs | Outputs | Quality |
|---|---|---|---|---|---|
| `onesource/models/game.py` (115L) | MLB game model. Shrunk recent scoring rate adjusted for opposing starter xFIP (over the innings starters cover) + bullpen FIP, park factors, HFA; 20k-draw Poisson Monte Carlo. | MLB | `TeamInputs` (runs/game, opp starter & bullpen xFIP, park factors) + `config` knobs | `GameProjection`: win prob, total mean, over-probs, run-line cover probs | **5** — canonical, transparent, backtested monotonic component-by-component (Brier 0.2483→0.2463). |
| `onesource/models/props.py` (186L) | MLB player props: Poisson Ks, Binomial hits (xBA), Poisson/neg-binomial total bases, per-PA HR. Blends our Statcast rate 50/50 with FantasyPros. | MLB | per-PA rates, FP projections, line | P(over) per market | **5** — well-calibrated (backtest gaps ≈0 except batter_hits +0.03), unit-tested. |
| `onesource/models/generic.py` (214L) | Cross-sport engine. Off/def ratings from recent ESPN scores shrunk to league avg (`RATING_SHRINK=0.65`), additive or multiplicative score method, Normal margin/total (basketball/football) or Poisson (NHL); Elo blended per `elo_blend`. Props via negative-binomial (counts) / Normal (yardage), dispersion by `NB_DISPERSION` keyword. | **NFL, NCAAF**, WNBA, NBA, NHL | results list, `Sport` config, projections | `GameProjection`-like (home_win_prob, total, over probs), `prop_prob_over` | **4** — clean and well-tested for the math, but football/NCAAF is *priming-tuned, not yet validated on live results* (no NFL/NCAAF games graded this offseason). |
| `onesource/models/elo.py` (60L) | Logistic Elo with HFA, MOV multiplier, between-season regression. Walk-forward safe. | all team sports w/ `elo_blend>0` | game results stream | rating dict, home win prob | **5** — small, pure, unit-tested (`test_elo.py`), backtested on WNBA. |
| `onesource/sports.py` (172L) | Sport registry — per-sport constants (league_ppg, hfa, sigma_margin/total, model type, Elo params, score_method, rest/qb coeffs, in-season months). **NFL & NCAAF entries fully populated.** | all | — | `Sport` dataclasses | **5** — central, documented, with backtest-derived tuning notes inline. |
| `onesource/pipeline.py` (1256L) | Daily orchestrator: builds slate, projects games/props, pulls BP lines, de-vigs, shrinks to market consensus (`MARKET_SHRINK`), computes EV + ¼-Kelly. Branches `_run_mlb` vs generic. Degrades gracefully without keys. | all | clients + models + config | projection DataFrames → `latest.json` | **4** — comprehensive and resilient (`test_pipeline_resilience.py`), but large (1256L); a consolidation candidate for decomposition. |

**Supporting analysis modules** (operate over the projected slate, mostly pure + unit-tested): `edge.py` (276L, market-consensus / arb / middles / low-hold), `calculators.py` (176L, de-vig/Kelly/odds math), `odds.py` (American odds primitives), `sgp.py` (Gaussian-copula same-game-parlay pricing), `experts.py` (model vs BP-expert vs public consensus), `scorecard.py` (model-vs-market skill split), `clv.py` (closing-line value), `lineshop.py` (best-price finder), `dfs.py` (PrizePicks/Underdog pick'em optimizer), `teamstats.py` + `playerlogs.py` (research cards & hit-rate splits — football market→stat maps present), `internal_stats.py` (replaces blocked pybaseball on CI), `nfl_history.py` (376L, parser for sportsoddshistory NFL results+lines).

### NFL & NCAAF readiness (launch target)
- Both run through `generic.py`, parameterized in `sports.py`. **NFL:** `model=normal`, sigma_margin 16.0, `elo_blend=0.60` (k=20), `rest_coeff=0.5`, `qb_coeff=0.0` (capability present but disabled — hurts CLV without real-time QB signal), FantasyPros weekly projections. **NCAAF:** sigma_margin 16.0, `elo_blend=0.50` (k=22, regress 0.45), FBS-only (`espn_params groups=80`).
- Tuning is **literature/backtest priming**, validated on historical closing lines (NFL log-loss 0.6385→0.6372 on 2019–2024) but **not yet forward-tested on live graded games** (offseason). Treat NFL/NCAAF as "known-good math, unproven live edge."
- Football props: `generic.prop_prob_over` + `playerlogs.py` market maps (passing/rushing/receiving yards, TDs, receptions, attempts). Validated with synthetic inputs in `tests/test_football.py`.

---

## 3. Data assets ("crown jewels")

All under `data/`, gzipped, loaded via `onesource/history.py`. Manifest: `data/history/README.md`. Total `data/history` ≈ 59 MB.

| Path | Contents | Approx size / coverage |
|---|---|---|
| `data/history/bp_odds/bp_game_odds_2026.jsonl.gz` | **BettingPros game odds with OPEN + CLOSE** (ML/run-line/total/team-total). | 9.3k rows, 2026-03→06. **🔒 IRREPLACEABLE — BP is live-only, cannot be re-pulled.** |
| `data/history/bp_odds/bp_first5_nrfi_2026.jsonl.gz` | BP first-inning / first-five (NRFI, F5) open+close. | 10.4k rows. **🔒 IRREPLACEABLE.** |
| `data/history/bp_odds/closing_consensus_2026.jsonl.gz` | Per-game consensus open/close fair ML probs. | 312 games. **🔒 IRREPLACEABLE.** |
| `data/history/closing_lines/{nfl,ncaaf?,mlb,nba,nhl,wnba}/*.jsonl.gz` | Closing odds per event/market/side/book. **NFL 2016–2025** (10 seasons), MLB/WNBA 2026, NBA 2020–2026, NHL 2021–2026. ⚠ no NCAAF closing lines. | ~1.9 MB. Paid-vendor (Odds API) derived → **🔒 do not regenerate.** |
| `data/history/snapshots/<sport>/<date>.jsonl[.gz]` | Append-only multi-book odds snapshots (last pre-game = closing line). Largest live store. | ~30 MB; MLB/WNBA daily files up to ~9 MB. Grows hourly. **🔒 forward CLV history.** |
| `data/history/backfill/nfl/<2016-2025>/games.json.gz` | NFL game results + line context (+2025 player_games). | 10 seasons. |
| `data/history/backfill/ncaaf/<2004-2025>/games.json.gz` | NCAAF game results (player logs intentionally omitted). | 22 seasons (deepest backfill). |
| `data/history/backfill/mlb/<2016-2026>/` | games, `statcast_xstats`, `player_games`, `game_context` (Retrosheet), linescores, starters, `people.json.gz`. | ~13 MB. player_games 2024/2025 ≈ 2.4 MB each. **Statcast-derived → 🔒.** |
| `data/history/backfill/{nba,wnba,nhl}/` | games + player box scores. WNBA 2002–2026, player logs 2018+. | — |
| `data/history/backtest/legacy/games_detail.csv.gz` | Graded game projections vs market vs actuals (179k rows). | 1.4 MB. |
| `data/history/backtest/legacy/props_detail.csv.gz` | Graded prop projections + lines + actuals (648k rows). | 7.4 MB (largest single file). |
| `data/history/backtest/legacy/history_*_multi.jsonl.gz` | Graded model-vs-market game history (NBA 7.6k, NHL 5.9k, MLB). | — |
| `data/history/elo/wnba_elo_pregame.json.gz` | Pregame Elo + rest days per WNBA game, 2002–2026 (5.8k). | 200 KB |
| `data/history/calibration/*.json` | Fitted calibration params from prior model (props a/b, game knots). | 308 KB |
| `data/history/statcast/*.json` | Pitcher arsenals, team whiff. | 256 KB. **Statcast-derived → 🔒.** |
| `data/history/park_factors.json` | Empirical MLB park run factors (computed from backfill). | 4 KB (regenerable via script). |
| `data/history/fantasypros/` | Every FantasyPros projection pull (replay source). | 888 KB. **Paid-API derived → 🔒.** |
| `data/history/markets/`, `bp_odds/raw`, `misc/` | BP markets catalog, raw book snapshots, misc graded props/consensus CSVs. | — |
| `data/output/` | `latest.json` (live site state) + `projections/<date>.json` archive. | 7.3 MB |
| `data/track/` | Forward-test ledgers: `results.jsonl`, `dfs_plays.jsonl`, `daily_record.jsonl`, pending/recap/confirmation state. | 132 KB. Grows hourly. |
| `reports/backtest_2026-06-12.{md,json}` | Latest walk-forward backtest output. | 13 KB |

**Crown-jewel flag:** the entire `data/history/bp_odds/` tree, `fantasypros/`, `statcast/`/`backfill/.../statcast_xstats`, `closing_lines/`, and the live `snapshots/` store are paid-API- or vendor-derived and **must never be regenerated/overwritten** — BettingPros and Odds API historical prices cannot be re-pulled. Note `*.parquet` is gitignored; current committed history is `.jsonl.gz`/`.json.gz`/`.csv.gz`.

---

## 4. Integrations

All clients in `onesource/clients/`. Keys resolved through `onesource/config.py::secret()` — **env var first, then Streamlit secrets** (so it works in Actions, local `.env`, and Streamlit Cloud).

| Client | API | Auth / key resolution |
|---|---|---|
| `clients/bettingpros.py` (683L) | BettingPros Public Partner API (`api.bettingpros.com/v3`) — lines, best prices, BP projections/EV/recommended side, DFS pick'em (PrizePicks/Underdog) book breakdowns. | `BP_PARTNER_KEY` in `x-api-key` header on every call; premium fields need `BP_USER` + `BP_USER_KEY` as `auth=user&user=&key=` query params. Throttled ~4 rps, 10-min disk cache. Market IDs in `config.BP_MARKET_IDS`. |
| `clients/fantasypros.py` (131L) | FantasyPros Public API (`api.fantasypros.com/public/v2/json`) — MLB daily + NFL weekly projections. | `FANTASYPROS_API_KEY` in `x-api-key` header. |
| `clients/oddsapi.py` (176L) | The Odds API (`the-odds-api.com/v4`) — multi-book game lines for the EDGES consensus/line-shop. | `THE_ODDS_API_KEY` as `apiKey` query param. Credit-frugal: 1 req/sport/hr, regions `us,us2`, hard credit floor `ODDS_API_MIN_CREDITS=1000`. |
| `clients/espn.py` (332L) | ESPN public scoreboard — slate + scores for WNBA/NBA/**NFL/NCAAF**/NHL. | Free, no key. |
| `clients/mlb_statsapi.py` (295L) | MLB StatsAPI — slate, probables, lineups. | Free, no key. |
| `clients/statcast.py` (62L) | pybaseball / Statcast (FanGraphs + xstats). | Free; blocked (403) on CI → `internal_stats.py` fallback. |
| `weather.py` (80L) | Open-Meteo ballpark weather. | Free, no key. MLB only. |
| `notify.py` | ntfy.sh push notifications. | `NTFY_TOPIC` (+ optional `NTFY_SERVER`/`NTFY_TOKEN`). |
| `ai.py` (85L) | Anthropic (in-app AI analyst, Claude Opus 4.8). | `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN`; model via `OSP_AI_MODEL`. Optional — degrades to copy-paste brief. |

Required secrets (see `.env.example`): `FANTASYPROS_API_KEY`, `BP_PARTNER_KEY`, `BP_USER`, `BP_USER_KEY`, `APP_PASSWORD` (dashboard gate, constant-time compare in `app/auth.py`). Optional: `THE_ODDS_API_KEY`, `ANTHROPIC_API_KEY`, `NTFY_*`.

---

## 5. Tests

- **Framework:** pytest. **287 test functions across 34 files** in `tests/`. No `pytest.ini`/`pyproject.toml`/`conftest.py` — runs with default discovery.
- **Run:** `pytest` from repo root.
- **Coverage:** broad and central to the design — odds math (`test_calculators`, `test_odds`, `test_oddsapi`), every model (`test_models`, `test_elo`, `test_generic`, `test_sport_models`, `test_football`, `test_props` via models), edge/arb (`test_edge`), CLV (`test_clv`), scorecard, experts, sgp, dfs, lineshop, pipeline resilience, history loaders, NFL parser (`test_nfl_history`), and the Streamlit UI via AppTest harness (`test_ui`, `test_assets`). `test_sport_models.py` + `test_football.py` specifically lock NFL/NCAAF readiness with synthetic inputs (favorites favored, HFA tilt, monotonic totals, Elo responding, valid prop probabilities).
- **Gap:** football tests are synthetic-only; no live-graded NFL/NCAAF validation exists yet (offseason).

---

## 6. Dependencies (`requirements.txt`)

```
pandas>=2.0   numpy>=1.26   scipy>=1.11   requests>=2.31
pybaseball>=2.2.7   streamlit>=1.36   python-dotenv>=1.0
pyarrow>=15   altair>=5   anthropic>=0.49
```
No `pyproject.toml`/lockfile. `pybaseball` is the only heavy/fragile dep (403 on CI → `internal_stats.py` fallback). `anthropic` optional at runtime.

---

## 7. Migration recommendation

Source commit: `d2897f20ca8e48a7ef21923bd5da7933a2a39944`.

| Component | Recommendation | Reason |
|---|---|---|
| `onesource/models/generic.py` + `sports.py` + `elo.py` | **MIGRATE** | Canonical cross-sport engine; the NFL/NCAAF launch path. Well-tested, parameterized, ready to flip on. |
| `onesource/models/game.py` + `props.py` (MLB) | **MIGRATE** | Deepest, best-validated models (Brier/calibration proven). Keep as the MLB reference even if NFL launches first. |
| `onesource/pipeline.py` | **MIGRATE (NEEDS-REVIEW)** | Essential orchestrator but 1256L; review for decomposition during consolidation. Resilient + tested. |
| `onesource/edge.py`, `calculators.py`, `odds.py`, `clv.py`, `lineshop.py`, `sgp.py`, `scorecard.py` | **MIGRATE** | Pure, unit-tested sharp/odds layer — sport-agnostic, high reuse value. |
| `onesource/clients/*` | **MIGRATE** | The integration layer (BP/FP/Odds/ESPN/StatsAPI). Centralized key handling. Carry as-is. |
| `data/history/` (bp_odds, closing_lines, snapshots, statcast, fantasypros, backfill) | **MIGRATE — PRESERVE VERBATIM** | Crown jewels; paid-API/vendor-derived and irreplaceable. Copy bit-for-bit, never regenerate. |
| `onesource/replay.py` + `scripts/rebuild_site.py` | **MIGRATE** | Enables credit-free rebuilds against the library — critical operational capability. |
| `onesource/nfl_history.py` + `internal_stats.py` | **MIGRATE** | NFL ingest parser (launch-relevant) and CI-safe stats fallback. |
| `experts.py`, `dfs.py`, `teamstats.py`, `playerlogs.py`, `ai.py`, `weather.py`, `notify.py` | **NEEDS-REVIEW** | Feature modules valuable but secondary to launch; migrate if the consolidation target keeps the same dashboard scope. `weather.py` MLB-only. |
| `app/` (Streamlit dashboard) | **NEEDS-REVIEW** | Migrate only if the consolidated product keeps Streamlit; otherwise it's a presentation layer to re-platform. |
| `scripts/notify_test.py`, `dump_bp_props.py`, `discover_markets.py` | **NEEDS-REVIEW** | One-off/diagnostic utilities; keep `discover_markets` (operational), the others are situational. |
| Duplicate/legacy upstream repos (Sports-projections, edge-equation-v1, Sports-stats-data, profit-hunt) | **SKIP** | Already curated into this repo's `data/history/` (2026-06-12). This repo supersedes them. |

**Bottom line:** OneSourceProjections is the correct migration base. Migrate the `onesource/` package + `data/history/` wholesale (preserving the crown-jewel data verbatim), prioritizing the generic engine + `sports.py` for the NFL/NCAAF launch. NCAAF has the deepest game backfill (2004+) but **no closing-line history** and **no live validation** — flag for review before sizing bets.
