# Historical data

Curated from prior EdgeCash repos (Sports-projections, edge-equation-v1,
Sports-stats-data) on 2026-06-12. Everything here is gzipped; load with
`onesource/history.py`. Sizes are small because only model-relevant
history was carried over — raw scrape dumps, vendor cards, and
non-modeled leagues (KBO/EPL/UCL, NCAA baseball/softball) were left behind.

| Path | Contents | Coverage | Source repo |
|---|---|---|---|
| `closing_lines/{mlb,nba,nhl,wnba}/2026.jsonl.gz` | Closing odds per event/market/side/book (moneyline, total; decimal + american) | May 2026 (MLB 74k rows, WNBA 22k, NHL 13k, NBA 12k) | edge-equation-v1 ∪ Sports-projections (deduped) |
| `results/<sport>/2026.jsonl.gz` | Final scores | 2026 season | edge-equation-v1 |
| `results/mlb/2022_2026_multi.jsonl.gz` | MLB final scores | 2022–2026 (9.8k games) | Sports-stats-data |
| `backfill/mlb/<year>/games.json.gz` | Game results + run-line/total context | 2016–2026 | Sports-projections |
| `backfill/mlb/<year>/statcast_xstats.json.gz` | Per-player xBA/xSLG/xwOBA aggregates by season | 2016–2026 | Sports-projections |
| `backfill/mlb/<year>/player_games.jsonl.gz` | Player box-score lines | 2021–2026 | Sports-projections |
| `backfill/mlb/<year>/game_context.jsonl.gz` | Retrosheet game logs: final scores + park, weather, umpires, attendance. For 2016–2021 this is the games source (no games.json those years) | 2016–2026 (no 2019) | Sports-projections |
| `backfill/mlb/2026/linescores.json.gz` | First-inning and first-5 scores per game (NRFI / F5 markets) | 2026 | Sports-projections |
| `backfill/mlb/people.json.gz` | MLBAM player id ↔ name/handedness map (4.6k players) | — | Sports-projections |
| `backfill/wnba/<year>/games.json.gz` | Game results | 2002–2026 | Sports-projections |
| `backfill/wnba/<year>/player_games.jsonl.gz` | Player box scores (pts/reb/ast/min...) | 2018–2026 | Sports-projections |
| `backfill/nba/`, `backfill/nfl/` | games + player box scores | recent seasons | Sports-projections |
| `backfill/ncaaf/<year>/games.json.gz` | Game results (player logs intentionally omitted) | 2004+ | Sports-projections |
| `backtest/legacy/games_detail.csv.gz` | Graded game projections vs market vs actuals (179k rows) | prior model | Sports-projections |
| `backtest/legacy/props_detail.csv.gz` | Graded prop projections w/ lines + actuals (648k rows) | 2024+ | Sports-projections |
| `backtest/legacy/history_*_multi.jsonl.gz` | Graded model-vs-market game history (NBA 7.6k, NHL 5.9k, MLB) | 2020+ | Sports-stats-data |
| `calibration/*.json` | Fitted calibration params from the prior model (props: per-market a/b; games: knots) | — | Sports-projections |
| `elo/wnba_elo_pregame.json.gz` | Pregame Elo + rest days per WNBA game (5.8k games) | 2002–2026 | Sports-projections |
| `statcast/pitcher_arsenals.json`, `team_whiff.json` | Pitch-mix and team whiff features | current | Sports-projections |
| `misc/Sports_2026_YTD_Historical_Props.csv` | Graded props with odds + results | 2026 YTD | Sports-projections |
| `misc/odds_snapshots.jsonl.gz` | Multi-book odds snapshots (3.3k rows) | 2026 | Sports-stats-data (odds.db) |
| `misc/mlb_closing_consensus_2026.jsonl.gz` | One-row-per-game consensus closers | May 2026 | Sports-stats-data |
| `misc/picks_log/` | Prior pick logs (MLB, WNBA) | 2026 | edge-equation-v1 |

## What this is for

1. **CLV benchmarking** — grade our openers against `closing_lines/`.
2. **Backtesting** — replay `backfill/<sport>/games.json` through the game
   models; grade props vs `player_games.jsonl`.
3. **Calibration** — `backtest/legacy/` shows where the prior model ran
   hot/cold (e.g. props_summary showed batter_hits over-projected by
   ~0.07); `calibration/` has its fitted corrections as a starting point.
4. **WNBA props** — `player_games.jsonl` 2018+ supports building real
   per-player rate models instead of leaning on BettingPros projections.

Not yet imported: `profit-hunt` (private repo, inaccessible from this
session) and anything in repos created later — re-run the curation
against those when access is available.
