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
| `bp_odds/bp_game_odds_2026.jsonl.gz` | **BettingPros game odds with OPEN + CLOSE** (moneyline, run-line, total, team-total) — open_cost/close_cost/close_best/close_median/n_books/projection/cover_prob per event/market/side | 2026-03-25 → 06-11 (9.3k rows) | profit-hunt |
| `bp_odds/bp_first5_nrfi_2026.jsonl.gz` | BettingPros first-inning / first-five markets, open + close (NRFI, F5 run-line/total) | 2026 (10.4k rows) | profit-hunt |
| `bp_odds/closing_consensus_2026.jsonl.gz` | Per-game consensus open/close fair moneyline probabilities | 2026 (312 games) | profit-hunt |
| `backfill/mlb/2026/starters.json.gz` | 2026 game→starter map (MLBAM ids + names) and per-pitcher season stats (era/whip/k9/bb9/hr9/ip) | 2026 (1066 games, 302 pitchers) | profit-hunt |
| `track/picks_ledger_2026.jsonl.gz`, `track/tenths_totals_ledger_2026.jsonl.gz` | Realized pick history (model prob, price, book, units, result) | 2026 | profit-hunt |
| `backtest/legacy/nrfi_2026-03-20_2026-06-05.csv.gz`, `edge_clv_report_2026.txt` | Prior NRFI/game backtest output + CLV report | 2026 | profit-hunt |

## What this is for

1. **CLV benchmarking** — grade our openers against `closing_lines/`.
2. **Backtesting** — replay `backfill/<sport>/games.json` through the game
   models; grade props vs `player_games.jsonl`.
3. **Calibration** — `backtest/legacy/` shows where the prior model ran
   hot/cold (e.g. props_summary showed batter_hits over-projected by
   ~0.07); `calibration/` has its fitted corrections as a starting point.
4. **WNBA props** — `player_games.jsonl` 2018+ supports building real
   per-player rate models instead of leaning on BettingPros projections.

### On the BettingPros open/close odds (`bp_odds/`)

This is the highest-value odds history we have. BettingPros is a live-only
API — you cannot retroactively pull historical opening prices — so these
captured snapshots are irreplaceable, and they come from the *same source*
the live pipeline uses (unlike the Odds API closing lines in
`closing_lines/`, which came from a different vendor). They carry **both
opening and closing** prices across the full 2026 season to date, which
enables true CLV measurement (did our number beat the open→close move?)
rather than just grading at the close. `profit-hunt` contained **no raw
Odds API data** — those credits were spent in the other repos and that
data is already in `closing_lines/`.

Skipped from profit-hunt: a 235 MB MLB StatsAPI response cache (free,
re-pullable), daily pick-graphic PNGs, and HTML site output.

Sources fully imported: Sports-projections, edge-equation-v1,
Sports-stats-data, profit-hunt. Re-run the curation against any repos
created later when access is available.
