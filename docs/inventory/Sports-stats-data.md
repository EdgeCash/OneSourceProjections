# Inventory: Sports-stats-data

**Repo:** `/home/user/Sports-stats-data`
**Source commit:** `da4658948b57837e909aad9943b31f397af8f725`
**Last activity:** 2026-06-24 18:07:17 +0000 ("chore: build site")
**Size:** 52 MB, 239 files (data dir = 30 MB)
**Package:** `espn-aggregator` v0.2.0 (Python >=3.10, MIT)
**Inventoried:** 2026-06-24 (read-only)

---

## 1. Overview

This is a personal sports research hub. It aggregates teams/rosters/schedules/scores/stats/odds for every sport ESPN exposes (plus deeper league APIs), builds matchup cards (HTML/JSON), and generates AI-ready betting briefings. The same repo holds **both the code (`src/espn_aggregator/`) and the committed data store** — it is the canonical data store for the system.

**Organization:**
- `data/` (30 MB) — the primary committed data store: `metrics/` (team metric warehouse, per-league dated JSON), `history/` (game results & gamelogs, JSONL), `backtest/` (walk-forward projection vs result ledgers, JSONL), `odds.db` (SQLite line-movement snapshots), `odds_raw/` (manual xlsx drop-in, instructions only).
- `src/espn_aggregator/data/` — last-known-good API snapshots replayed when live pulls are blocked (nflverse EPA, Baseball Savant, NPB, oddsapi cache, play ledgers).
- `src/espn_aggregator/sources/`, `odds/` — API adapters and the odds line store.
- `scripts/` — refresh/backfill + validation scripts.
- `tests/` (+ `tests/fixtures/`) — pytest suite tested offline against fixtures.
- `.github/workflows/` — 17 scheduled/on-demand GitHub Actions that fetch live data and commit it back.

**Refresh model:** GitHub Actions runners (open egress) pull live data on cron schedules and commit results into `data/`. The library core is intentionally **dependency-free (stdlib urllib)**; `requests` used if present. Data is committed in-repo (not externalized to object storage), so the repo doubles as the durable store.

---

## 2. Datasets

### `data/metrics/` — Team Metric Warehouse (per-league, dated JSON snapshots)
Each file = one league's full per-team metric set on a given date. Structure: `{league, date, teams:[{team_id, abbr, sport, league, values:{<metric>:{value, splits:{season,home,away,last10,last5}, rank, rank_color}}}]}`.

| Path | Sport | Size | Snapshots | Teams | Metrics/team |
|---|---|---|---|---|---|
| `data/metrics/nfl/2026-06-{05,09,16,23}.json` | **NFL** | 128 KB each (516 KB total) | 4 dates (2026-06-05 → 06-23) | 32 | 21 |
| `data/metrics/college-football/2026-06-{05,09,16}.json` | **NCAAF** | ~2.4 MB each (7.1 MB total) | 3 dates (2026-06-05 → 06-16) | **755** | 17 |

### `data/history/` — Results & Gamelogs (JSONL, 12 MB)
| Path | Sport | Size | Lines | Schema (keys) |
|---|---|---|---|---|
| `mlb_results_multi.jsonl` | MLB | 1.2 MB | 9,810 | season, date, status, home_team, away_team, home_score, away_score (seasons 2022–2025, dates 2022-04-01 → 2025-11-01) |
| `mlb_results_2026.jsonl` | MLB | 264 KB | — | 2026 results |
| `mlb_closing_2026.jsonl` | MLB | 60 KB | — | closing lines 2026 |
| `mlb_pitcher_logs_multi.jsonl` | MLB | 3.6 MB | — | per-game pitcher ER/IP logs |
| `mlb_pitcher_gamelogs_2026.json` | MLB | 192 KB | — | 2026 pitcher gamelogs |
| `mlb_statcast_pitchers.jsonl` | MLB | 1.7 MB | 7,280 | date, season, player_id, name, team, xwoba_allowed, xba_allowed, xslg_allowed, xera, barrel_pct, hardhit_pct, exit_velo, sweetspot_pct |
| `nba_games_multi.jsonl` | NBA | 1.2 MB | 8,072 | season, date, game_id, home, away, home_pts, away_pts, home_poss, away_poss |
| `nba_closing_multi.jsonl` | NBA | 940 KB | — | closing lines |
| `nhl_games_multi.jsonl` / `nhl_closing_multi.jsonl` | NHL | 740/736 KB | — | games + closing lines |
| `wnba_games_multi.jsonl` | WNBA | 292 KB | — | games |
| `mls_games_multi.jsonl` | MLS | 492 KB | — | games |

### `data/backtest/` — Walk-Forward Projection Ledgers (JSONL, 9.4 MB)
Schema: `date, league, sport, away, home, proj_home, proj_away, proj_margin, proj_total, home_win_prob, method, mkt_spread_home, mkt_total, mkt_home_ml, mkt_away_ml, actual_home, actual_away, graded`.
| Path | Sport | Size | Lines |
|---|---|---|---|
| `history_mlb_multi.jsonl` | MLB | 2.9 MB | 9,188 |
| `history_nba_multi.jsonl` | NBA | 2.5 MB | — |
| `history_nhl_multi.jsonl` | NHL | 1.9 MB | — |
| `history_wnba_multi.jsonl` | WNBA | 684 KB | — |
| `history_mls_multi.jsonl` | MLS | 616 KB | — |
| `history_mlb.jsonl` / `history_mlb_pitchers.jsonl` | MLB | 344 KB each | — |
| `projections.jsonl` | mixed | 168 KB | 494 (mlb 311, mls 71, wnba 66, nhl 24, nba 22) |

### `data/odds.db` — Line-Movement Store (SQLite, 1.9 MB)
Single table `odds_snapshots` (9,036 rows). Columns: ts REAL, sport TEXT, event_id, commence_time, home_team, away_team, book, home_ml INT, away_ml INT, spread REAL, total REAL.
- Sports: **college-football, mlb, mls, nba, nfl, nhl, wnba**.
- Books: BetMGM, BetOnline.ag, BetRivers, BetUS, Bovada, Caesars, DraftKings, FanDuel, Fanatics, LowVig.ag, MyBookie.ag (+ DraftKings live variants).
- ts range: 2026-06-02 → 2026-06-24.
- NOTE: `data/odds.db` is **gitignored locally** (`/data/odds.db`) but the daily Action commits its own copy under `data/` on CI — so the committed file is CI-generated and grows over time.

### `src/espn_aggregator/data/` — Last-Known-Good API Snapshots (replay cache)
- `nflverse_2025.json` — **NFL** team EPA priors, 32 teams: `{epa_play, def_epa_play, success_rate}` per team abbr. Derived from nflverse PBP (free GitHub releases).
- `savant_2026.json` — **MLB** Baseball Savant team xStats: `{barrel_pct, exit_velo, hardhit_pct, sweetspot_pct, xba, xslg, xwoba, xwoba_allowed}` per team.
- `npb_2026.json` — Japanese NPB baseball.
- `data/oddsapi/*.json` — cached The Odds API responses (MLB h2h/spreads/totals, WNBA h2h) for 2026-06-22..24.
- `data/plays/<YYYYMMDD>.json` — 21 daily play/slate ledgers (2026-06-04 → 06-24).

**Paid / hard-to-regenerate data (crown jewels — DO NOT regenerate):**
- **`mlb_statcast_pitchers.jsonl`** — point-in-time per-pitcher Statcast captured day-by-day; the snapshot workflow notes it "can't be reconstructed after the fact." Baseball Savant 403s datacenter IPs, so this cannot be re-pulled from CI.
- **`savant_2026.json`** — same Savant source; kept as replay cache because live Savant pulls get blocked.
- **`data/oddsapi/*.json`** + **`odds.db`** (The Odds API rows) — The Odds API is a metered/paid-tier service (`ODDS_API_KEY`); historical snapshots consume quota and should not be re-fetched.
- All `*_closing_*.jsonl` closing lines — partly built from browser-only archives that block CI; non-trivial to reproduce.

---

## 3. Schemas (key NFL & NCAAF datasets)

**NFL metric warehouse** (`data/metrics/nfl/`) — 21 metrics/team:
`win_pct, ppg, opp_ppg, point_diff, pythag_win, adj_net, sos, ats_cover, ats_record, over_pct, ou_record, ats_as_fav, ats_as_dog, avg_rest, yards_per_play, opp_yards_per_play, third_down_pct, redzone_td_pct, turnover_margin, epa_play, def_epa_play, success_rate`.
Each metric: `value`, `splits{season,home,away,last10,last5}`, `rank`, `rank_color`. Many betting metrics (ats_*, over_pct) are currently null (off-season). `epa_play/def_epa_play/success_rate` sourced from nflverse.

**NCAAF metric warehouse** (`data/metrics/college-football/`) — 17 metrics/team, **755 teams**:
`win_pct, ppg, opp_ppg, point_diff, pythag_win, adj_net, sos, ats_cover, ats_record, over_pct, ou_record, ats_as_fav, ats_as_dog, avg_rest, third_down_pct, redzone_td_pct, turnover_margin`. Note: lacks `epa_play/success_rate` in the snapshot unless a CFBD key is present at fetch time (those map from CFBD offense/defense PPA).

**Metric registry** (`src/espn_aggregator/metrics/registry.py`) is the declarative single-source-of-truth catalog defining every metric (key, label, category, sport, fmt, source, splits). Football sources: `espn_season` and `nflverse`/`cfbd`.

---

## 4. Refresh / Ingestion Code

**API adapters** (`src/espn_aggregator/sources/` and `odds/`):

| Adapter | Endpoint | Key resolution |
|---|---|---|
| ESPN (core `client.py`) | ESPN public site/scoreboard APIs | none (free) |
| `sources/cfbd.py` | `https://api.collegefootballdata.com` | **`CFBD_API_KEY`** env (free key; Bearer header). Degrades to `{}` if absent. NCAAF advanced PPA. |
| `sources/nflverse.py` | `github.com/nflverse/nflverse-data/releases/.../play_by_play_{season}.csv.gz` | none. `NFLVERSE_REFRESH=1` forces re-pull; else reads committed snapshot. |
| `odds/theoddsapi.py` | `https://api.the-odds-api.com/v4` | **`ODDS_API_KEY`** env (`os.environ.get`); raises if unset. **Paid/metered.** |
| `sources/mlb_statsapi.py` | `https://statsapi.mlb.com/api` | none |
| `sources/savant.py` | `https://baseballsavant.mlb.com/leaderboard` | none (but 403s CI; replay cache used) |
| `sources/moneypuck.py` | `https://moneypuck.com/.../teams.csv` | none (xGF%/Corsi) |
| `sources/nhl_web.py` | `https://api-web.nhle.com/v1`, `https://api.nhle.com/stats/rest/en` | none |
| `sources/asa.py` | `https://app.americansocceranalysis.com/api/v1` | none |
| `sources/npb.py` | `https://spaia.jp/baseball/npb/api/...` | none |
| `sources/draftkings.py`, `bovada.py` | DK/Bovada sportsbook APIs | none (browser-style headers) |
| `sources/odds_history.py` | nflverse games.csv, aussportsbetting, sportsbookreviewsonline | none; xlsx archives block CI (manual `data/odds_raw/` drop-in) |

**Refresh scripts** (`scripts/`): `fetch_statcast_historical.py` (pybaseball pitch-level backfill, **run locally** — Savant 403s datacenter IPs), `derive_wnba_history.py`, `tune_projections.py`, `dry_run.py`, `live_card.py`, plus `setup.sh`.

**Workflows** (`.github/workflows/`, 17): `fetch-football.yml` (NFL EPA via nflverse + NCAAF via ESPN/CFBD, weekly Tue), `statcast-snapshot.yml` (daily pitcher Statcast append), `fetch-pitchers.yml`, `fetch-nhl.yml`, `fetch-mls.yml`, `fetch-npb.yml`, `fetch-odds.yml`, `odds-snapshot.yml` (commits `odds.db`), `daily-slate.yml`, `backtest.yml`, `build-site.yml`/`pages.yml`, `ci.yml`, `live-test.yml`, diagnostics/probes. Keys resolve from repo secrets (`CFBD_API_KEY`, `ODDS_API_KEY`).

---

## 5. Tests / Validation

- `tests/` — 24 pytest modules tested offline against `tests/fixtures/` (incl. `nflverse_games.csv`, `nflverse_pbp.csv`, `savant_pitcher_statcast.csv`, `moneypuck_teams.csv`, `statsapi_*.json`). Football-relevant: `test_sources.py`, `test_integration.py`, `test_card_and_render.py`, `test_research_hub.py`. Others cover MLB stats/enrichment, market/no-vig math, ratings, metrics, backtest, NPB, inning markets.
- **Data-validation scripts:** `validate_statcast_mae.py` (Total MAE vs actual runs across seasons), `validate_statcast_totals.py`, `validate_innings.py`. These validate model/data quality rather than schema integrity.

---

## 6. Dependencies

Core library: **zero deps (stdlib only)**. Optional extras (`pyproject.toml`):
- `http`: requests>=2.28 (pooled HTTP, optional)
- `history`: openpyxl>=3.1 (xlsx odds archives)
- `statcast`: pybaseball>=2.2, pandas>=1.5, pyarrow>=12 (local Statcast backfill only — never imported by the library)
- `dev`: pytest>=7, ruff>=0.1

Console script: `espn-agg`. Hosting: Vercel (`vercel.json`, serves static `public/`) or GitHub Pages.

---

## 7. Migration Recommendation

**Source commit:** `da4658948b57837e909aad9943b31f397af8f725`

**Migrate (all canonical data):**
- **`data/` in full (30 MB)** — metrics warehouse, history JSONL, backtest ledgers, `odds.db`. This is the durable store. Largest files (>1 MB) are good Git LFS candidates: `mlb_pitcher_logs_multi.jsonl` (3.6 MB), `history_mlb_multi.jsonl` (2.9 MB), the three CFB metric snapshots (~2.4 MB each), `history_nba_multi.jsonl`, `mlb_statcast_pitchers.jsonl`, `mlb_results_multi.jsonl`, `odds.db`. Move data under Git LFS; the JSON/JSONL are line-oriented and compress/diff acceptably even without it.
- **`src/espn_aggregator/data/` replay snapshots** — small but load-bearing (`savant_2026.json`, `nflverse_2025.json`, oddsapi cache, play ledgers). Migrate verbatim.
- The full `src/`, `scripts/`, `tests/`, `.github/workflows/` (ingestion is inseparable from the data) and `pyproject.toml`.

**Crown jewels — migrate but NEVER regenerate:** `mlb_statcast_pitchers.jsonl` & `savant_2026.json` (point-in-time Savant, can't be rebuilt; datacenter IPs 403'd), The Odds API rows in `odds.db` + `data/oddsapi/*.json` (paid/metered quota), and `*_closing_*.jsonl` (browser-only archives). Preserve git history for these so the day-by-day capture chain is intact.

**Skip / regenerable:** `public/` & `docs/*.html` (built site, regenerated by `build-site.yml`), `research/*.html` mockups, `examples/`, `out/`/`.cache/`/`data/cache/` (already gitignored). NFL nflverse EPA and NCAAF ESPN/CFBD metrics are freely re-pullable, but cheap to carry — migrate for continuity.

**NFL/NCAAF coverage & quality note:** NFL = 32-team warehouse, 21 metrics incl. EPA (nflverse) + 4 dated 2026 off-season snapshots; betting/ATS metrics currently null (pre-season). NCAAF = exceptionally broad at **755 teams** × 17 metrics but lacks EPA/success_rate in committed snapshots (needs `CFBD_API_KEY` at fetch time). Neither sport yet has committed historical game-result or closing-line JSONL (history/ currently covers MLB/NBA/NHL/WNBA/MLS only) — a coverage gap to flag: football data is metric-warehouse + EPA priors only, no game-level result store.
