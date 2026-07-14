# 360Five

**360° of research. 5 questions. 365 days.**

A straight-shooting, multi-sport projection engine — **MLB, WNBA, NBA, NFL,
NCAAF, NHL** — projecting games (moneyline / total / spread) and player props,
with edges computed against market lines and a private Streamlit dashboard.
Every game is a full-circle **Edge Card** that answers the 5 W's — Who, What,
When, Where, Why — so there's nothing left to look up. Data, projections, and
tools mixed with a little wagering advice; every pick graded in public, wins and
losses, and the trigger is always yours.

**Personal research. Not financial advice. No bankroll promises. Bet responsibly.**

> **Brand & voice:** see [`docs/BRAND.md`](docs/BRAND.md); the visual system and
> Edge Card spec are in [`docs/DESIGN_SPEC.md`](docs/DESIGN_SPEC.md) (with a
> reference render at [`docs/design/edge-card.html`](docs/design/edge-card.html)).
> The product brand is *360Five* (by EdgeCash); **54.7** — the break-even-to-pro
> win rate — is kept as the methodology number. The Python package stays
> `project547`; the GitHub repo stays `OneSourceProjections` for history/links.

> **Consolidation note (June 2026):** this repo is now the canonical home for
> EdgeCash's projection work — the best modules from the older repos were
> consolidated here (see [`docs/CONSOLIDATION.md`](docs/CONSOLIDATION.md)).
> Research on the most accurate public NFL/NCAAF models and the resulting
> upgrade plan live in [`docs/research/`](docs/research/00-synthesis.md) and
> [`docs/ACCURACY_ROADMAP.md`](docs/ACCURACY_ROADMAP.md). The headline finding:
> the betting/edge math is already strong; the biggest accuracy lever is rating
> teams on **opponent-adjusted EPA/play** (new: `project547/epa.py`,
> `clients/nflverse.py`, `clients/cfbd.py`) instead of points — staged for
> validation before going live.

## How it works

```
MLB StatsAPI ──► MLB slate, probables, lineups, form ──┐
ESPN API     ──► other sports: slates + recent scores ─┤
pybaseball   ──► FanGraphs rates + Statcast xBA/xSLG ──┼──► models ──► P(outcomes)
FantasyPros  ──► daily/weekly player projections ──────┤                  │
BettingPros  ──► lines, best prices, BP projections ───┘    de-vig, EV, ¼-Kelly
                                                                          │
                                              data/output/latest.json ◄───┘
                                                          │
                                              Streamlit dashboard (password-gated)
```

The pipeline runs every sport that's in season (`project547/sports.py`
defines the calendar; override with `--sports`).

### MLB (the deep model)

- **Game model** (`project547/models/game.py`): recent team scoring rate
  shrunk toward league average; opposing **starter** quality applied over
  the innings starters cover and opposing **bullpen** quality over the
  rest (each as FIP / league FIP); **park factors** applied to the venue
  after de-biasing each team's own home park; plus home field. 20k-draw
  **negative-binomial** Monte Carlo (ties resolved as extra innings) → win
  prob, over/under probs, run-line cover probs. Runs are drawn from a negative
  binomial, not a Poisson: real MLB runs are heavily overdispersed (measured
  var/mean ≈ 2.3), so Poisson under-priced the totals tails and was overconfident
  on moneylines. Tuning the dispersion on the 2024–26 backtest flips totals-bet
  ROI vs closing from −9.1% (Poisson) to +1.9% and improves moneyline log-loss;
  the optimum (2.3) matches the measured dispersion. Park factors are derived
  empirically (`scripts/compute_park_factors.py` →
  `data/history/park_factors.json`, loaded via `project547/parks.py`).
  Backtested 2024–2026, each component improves the model monotonically
  (Brier 0.2483→0.2463, total-runs MAE 3.60→3.55, favorite hit-rate
  0.540→0.552); open→close CLV is +12.8% moneyline ROI at opening prices.
  The current season's game log is kept fresh by `scripts/build_mlb_backfill.py`
  (assembles `backfill/mlb/<season>/games.json.gz` from results + linescores and
  extends it daily from statsapi), so the model runs on the live season rather
  than silently falling back to the prior year.
- **Prop models** (`project547/models/props.py`): Poisson for Ks and total
  bases, binomial for hits, per-PA rate for HRs. Our Statcast-informed
  rates are blended 50/50 with FantasyPros projections when available.

### WNBA / NBA / NFL / NCAAF / NHL (the generic engine)

- **Game model** (`project547/models/generic.py`): offensive/defensive
  ratings from recent final scores (ESPN), shrunk toward league average,
  plus home advantage. Basketball/football use a Normal margin/total
  model; NHL uses the same Poisson simulation as MLB. Per-sport constants
  (league scoring, HFA, volatility) live in `project547/sports.py`. For
  sports with `elo_blend > 0`, an Elo rating system
  (`project547/models/elo.py`, maintained live from results) is blended
  into the moneyline win probability. WNBA uses 0.35 off/def + 0.65 Elo,
  which backtests to Brier 0.227 → **0.215** (favorite hit-rate
  0.62 → 0.67), well-calibrated across a 0.2–0.9 range. The Elo (and rest)
  adjustment is folded back into the projection via `with_consistent_margin`,
  so the published numbers always agree with each other: for normal-model
  sports the margin **and the side scores** are re-derived from the blended
  win prob (moneyline, spread cover, and the displayed "Team A x.x / Team B
  y.y" all carry the same information); for the Poisson sports (NHL) the
  score lambdas are tilted to match the blended win prob while holding the
  total, so the puck line and totals see the Elo/rest signal too. The NHL
  simulation resolves regulation ties with one decisive OT/shootout goal
  (margins of ±1, totals settle OT-inclusive, the way books grade). Elo
  seasons are labeled by league year (an NBA season spanning New Year is one
  season), results feeds are paged past ESPN's per-request cap and filtered
  to regular season + playoffs, and neutral-site games skip both the model's
  home advantage and Elo's home edge.
- **Props**: BettingPros `/props` supplies every line plus their premium
  projection; FantasyPros daily projections blend in where they exist
  (NBA). Our distribution layer converts the blended projection into
  P(over) — **negative binomial** for box-score counting stats (points,
  rebounds, assists, etc.), with per-market dispersion tuned against
  walk-forward calibration, since these stats are heavily overdispersed
  and right-skewed; Normal for yardage. This removed a large over-bias in
  the old Poisson/Normal layer (WNBA points calibration gap +0.08 → −0.01).
  Then EV on both sides and a Kelly stake on whichever side is positive.

### Edges

Model probability vs the best available price from BettingPros → EV per
unit and quarter-Kelly stake, for every game market and prop. Whole-number
lines (totals, spreads, integer prop lines) carry the model's push mass
explicitly — a push is a stake refund, neither a win for the under nor a
loss for the over — and every Kelly stake is sized from the same
calibrated, market-blended probability the EV was computed from, never the
raw model probability.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in your keys
python scripts/run_daily.py                       # all in-season sports
python scripts/run_daily.py --sports WNBA,MLB     # or pick specific ones
python scripts/build_static.py                    # build the static site -> site/
```

Then open `site/index.html`, or serve locally with `python -m http.server -d site`.

Required secrets (env vars or `.env`):

| Name | Purpose |
|---|---|
| `FANTASYPROS_API_KEY` | FantasyPros public API key (`x-api-key` header) |
| `BP_PARTNER_KEY` | BettingPros partner key, sent as `x-api-key` on every call |
| `BP_USER`, `BP_USER_KEY` | BettingPros premium tier: sent as `auth=user&user=…&key=…` query params to unlock projections/EV/recommended sides |
| `APPLY_CALIBRATION` | Optional — `1` applies post-hoc probability calibration (see `docs/MODEL_REPAIR.md`). |
| `ANTHROPIC_API_KEY` | Optional — analyst model access for offline scripts (the site's **Ask AI** chip is copy-paste and needs no key). |
| `OSP_AI_MODEL` | Optional — override the analyst model (default `claude-opus-4-8`). |

### API notes

- **BettingPros rate limits**: 5 req/sec, 5,000 req/day across all
  endpoints. The client throttles to ~4 RPS and caches responses for
  10 minutes; a full MLB slate run uses roughly a dozen requests.
- **BettingPros `/props`** supplies their projections, EV, and
  recommended side (premium fields) — shown on the dashboard as
  `bp_*` columns next to our model so you can see where you disagree
  with their consensus. Disagreement is where the interesting bets live.
- **FantasyPros MLB projections** use `type=daily&date=YYYY-MM-DD`:
  per-game projected stat lines that blend directly into the prop models.

### First-run checklist

1. `python scripts/discover_markets.py MLB` — prints your account's market
   IDs (id, slug, name, category). Update `BP_MARKET_IDS` in
   `project547/config.py` to match; the defaults are placeholders. The
   flatteners in `project547/clients/bettingpros.py` pull fields
   defensively, but spot-check one `/offers` and one `/props` response
   against them on first run.
2. `python scripts/run_daily.py` — should print game/prop counts. Batter
   props only appear once lineups are posted (~2-4h before first pitch).
3. `pytest` — odds math and model sanity checks.

## Hourly automation & forward-testing

`.github/workflows/hourly.yml` runs `scripts/hourly_update.py` every hour
(using the repo secrets), then commits the data files — which redeploys the
Streamlit app. Scheduled runs only fire on the **default branch**, so merge
this branch to activate; you can also trigger it manually from the Actions
tab.

Each run:

1. **Snapshots** current BettingPros odds for today + tomorrow into
   append-only logs (`data/history/snapshots/<sport>/<date>.jsonl`). The
   last pre-game snapshot per event becomes that game's closing line — this
   builds our own open/close history from the same source the model uses,
   so CLV/ROI can be measured going forward (and gives WNBA the open/close
   data MLB already had).
2. **Projects** today and tomorrow's slates and archives each
   (`data/output/projections/<date>.json`) so they can be graded later.
3. **Grades** games that have finished into `data/track/results.jsonl`
   (idempotent), tracking the model's win-probability Brier on every game
   and the realized P&L of model-recommended bets at projection-time prices.
4. **Writes** `data/output/latest.json` with both slates and a live
   performance summary; the dashboard's **Performance** tab reads it.

Forward-testing starts the moment the schedule is live: tomorrow's slate is
projected and archived now, and graded once those games finish. The longer
it runs, the more closing-line history and graded results accumulate.

> Note: scheduled Actions need the workflow on the default branch. Merge
> `claude/nifty-hamilton-26x2c1` to `main` to begin the hourly cadence.

## Prop research (hit-rate heatmaps & trend charts)

Each prop carries a hit-rate heatmap — how often the player has gone over
that line in their last 5 / 10 / 20 games, the season, and head-to-head vs
the opponent — computed from our own box-score logs
(`project547/playerlogs.py`) and shown as a red→green gradient on the Props
view. Selecting a prop draws a bar chart of the player's recent games
against the line (green = over, red = under). Logs come from the imported
backfill plus a forward store the hourly job appends from MLB boxscores, so
the splits stay current as the season passes the backfill cutoff. (WNBA
forward log ingestion is the next addition; its splits are current through
the import for now.)

## Game research cards

Each game has a full matchup breakdown (`project547/teamstats.py` +
`app/ui.research_card_html`): the team's offense compared to the opponent's
matching defense across Season/L10/L5 with **league ranks** and a star
**advantage** flag where the offense out-ranks the defense it faces, plus
model gauges (moneyline / total with PLAY/PASS) and — for MLB — game
trends (NRFI%, F5 win%, RL cover%, Over%, Pythagorean). The **NRFI** model
(`project547/models/nrfi.py`) is shown as *information*, not a bet: graded
against the real first-inning market on 2026 it is well-calibrated but doesn't
beat the price (model Brier 0.251 vs market 0.248, −4% ROI), so P(YRFI) is a
research lean and is graded forward for calibration, never routed to plays/EV.
Team identity is
resolved through `project547/teams.py` so full names, cities, and
abbreviations all join. Stats are derived from our box-score logs; a few
reference stats we don't capture (e.g. WNBA paint points, fast break) are
omitted. Generate a static HTML preview of all the graphics with
`python scripts/make_preview.py --sport WNBA`.

The **premium matchup card** (`app.ui.matchup_card_html`) is the full
team-vs-team research graphic: team panels (record / streak / recent results /
**Elo power** & **strength-of-schedule** ranks), projected score, **ML / RL /
Total confidence gauges**, per-side **top-advantage star panels**, the mirrored
offense-vs-defense tables (with a separate **Supporting Statistics** section)
carrying league-rank pills and an advantage column, and MLB **game trends** —
driven by the model, so it tells you the *play*, not just that "stats are
clustered." A **recency-window toggle** (L5 / L10 / L15 / L20 / L30 / Season)
recomputes ranks, advantages, and SOS live (power is the engine's current Elo;
SOS averages opponent Elo over the window). The same renderer is screenshotted
by `project547/cardimage.py` (pre-installed Chromium, **cream or dark** theme)
and embedded in the workbook's **Research Hub** — one matchup page per game —
making the `.xlsx` a "website to go." Football (NFL/NCAAF) cards are wired and
validated, ready to light up at season open. See
[`docs/WORKBOOK.md`](docs/WORKBOOK.md#research-hub-the-website-to-go).

## Daily wager workbook (bring-your-own-odds)

A downloadable, **editable** Excel/Google-Sheets workbook of the day's slate
(`project547/workbook.py`; **🎯 Projections → 📥 Daily workbook**). The model's
win probability for every market is **locked**; you type the price *your*
sportsbook is showing into the yellow cells, and the edge, EV, and ¼-Kelly stake
recompute **in-cell** — in Excel *or* Google Sheets, offline. Odds are the only
thing that changes all day, so they're the only thing you edit — which also
sidesteps the stale-line problem that plagues every live +EV tool. Tabs: Read
Me, Settings (bankroll / Kelly fraction reprice everything), Top Plays (biggest
engine-priced edges), per-sport Games & Props, and Track Record (losses
included). Every formula mirrors `project547/odds.py`, and where the engine
stored an EV the sheet recovers the exact probability it bet on, so the workbook
and the engine never disagree. The hourly job rebuilds it into
`data/output/workbook/latest.xlsx` (best-effort; it can never sink the run), and
the dashboard download button builds it fresh on demand. Build manually with
`python scripts/build_workbook.py`. Full design notes: [`docs/WORKBOOK.md`](docs/WORKBOOK.md).

## Comparison pipeline — daily Excel workbook

A separate, flat A/B pipeline (`sports_wagering_pipeline/`) runs beside the main
engine as a performance/architecture comparison: a SQLite-cached ingest, a PuLP
salary-cap DFS optimizer, and a CDF-based Pick'em +EV engine. It reuses the main
engine's **warm** FantasyPros/BettingPros cache (keyless, cache-hit only), so it
adds **zero** API calls, and the hourly job rebuilds its own Excel workbook once
a day (best-effort; it can never sink the run).

**⬇️ [Download the latest comparison workbook (`latest.xlsx`)](https://github.com/EdgeCash/OneSourceProjections/raw/main/sports_wagering_pipeline/data/output/latest.xlsx)**
— tabs: Summary, Pickem (line, side, win %, edge vs 54.3%, per-stat projection,
book odds), DFS lineups, and a cache/source Run Log. Details:
[`sports_wagering_pipeline/README.md`](sports_wagering_pipeline/README.md).

## Per-sport game models

MLB has its own richer pipeline (Statcast, xFIP, park factors → Poisson Monte
Carlo). Every other sport runs through the generic engine (`models/generic.py`),
parameterized per sport in `project547/sports.py`: scoring environment
(`league_ppg`), home edge (`hfa`), the margin/total standard deviations that
turn projections into probabilities, a `normal` (basketball/football) or
`poisson` (hockey) distribution, and a log5-style `multiplicative` score method.

All team sports are **Elo-primed**: each carries a per-sport Elo (`elo_k`,
`elo_home_edge`, `elo_regress`) blended with the off/def ratings (`elo_blend`),
with cross-season carryover and between-season regression. This matters most at
**season openers** — without it, thin early-season samples collapse to league
average and every game is a coin flip; with it, the out-of-season sports (NBA,
NHL, NFL, NCAAF) are ready to flip on with prior-season strength baked in. The
blends are literature-based priming defaults to validate via **Performance →
Model vs market** once games accrue. Readiness is locked by
`tests/test_sport_models.py` (every sport: favorites favored, HFA tilts even
games home, valid/monotonic totals, and Elo responding to results).

## Model-vs-market scorecard (proof of independent skill)

`project547/scorecard.py` answers the question that matters most: does the model
add signal, or just echo the market? At grading time every game now stores both
our win probability and the market's de-vigged win probability, so the
scorecard splits graded games into where the model **agrees** vs **disagrees**
with the market and scores each — Brier, accuracy, and the model's Brier edge
over the market. If the model is better calibrated and more accurate *on the
disagreement bucket*, that's independent edge worth betting; if not, the edges
are noise and `MARKET_SHRINK` should rise. A bet-level view splits ROI/CLV into
contrarian vs with-the-market stances. It also runs **calibration-driven
tuning**: `optimal_shrink` scans the model↔market blend weight and reports the
`MARKET_SHRINK` value that *would have* minimized Brier over graded games (gated
behind a minimum sample), turning the config knob into a data-backed decision.
Shown under **Performance → Model vs market**; pure functions, unit-tested in
`tests/test_scorecard.py`.

## Multi-book edge scanner (the sharp layer)

`project547/edge.py` adds what the elite tools (OddsJam, Unabated) are built on:
edges measured against the **de-vigged market consensus**, not a single price.
It takes every book's price on a market from the captured Odds API snapshots,
strips the vig from each, averages the fair probabilities, and grades the best
available price against the consensus of the *other* books — a price is only
flagged +EV when it beats the market's own fair estimate. On top of consensus
it scans each slate for **arbitrage**, **middles** (totals/spreads with a line
gap), and **low-hold** soft markets. Everything is pure functions over
`{book: {side: price}}` dicts (unit-tested in `tests/test_edge.py`), with a
snapshot-store adapter (`slate_books` / `scan_slate`). The **EDGES** tab renders
all four; it lights up automatically as multi-book odds accumulate. The Odds API
ingestion pulls the `us,us2` regions (≈15+ books incl. ESPN BET, Fanatics) so
the consensus is sharper — set `ODDS_API_REGIONS` back to `us` to halve credit
spend.

## Expert consensus (searchable)

`project547/experts.py` builds a multi-source consensus per prop from three
*independent* reads: **our model** (`model_over_prob` → a lean), **BettingPros'
expert recommendation** (`bp_recommended_side` + their `bp_bet_rating` ★
confidence — premium fields populated by the `BP_USER` auth), and the **public**
(`pick_pct_over` pick distribution). The **Experts** tab ranks props by how many
sources agree (✅ when all do), with a top-bar search by player/team/market —
so you can quickly check where the experts, the public, and the model line up.
Pure over the published slate, unit-tested in `tests/test_experts.py`.

## SGP correlation finder

`project547/sgp.py` prices same-game parlays through the Gaussian copula
(`project547.calculators`) using correlation **priors** for common leg
relationships (`CORRELATION_PRESETS`). Given each leg's win probability and a
correlation, `price_sgp` returns the correlation-adjusted joint probability, the
fair vs naive-independent prices, the "lift", and — with the book's quoted SGP
price — the EV and ¼-Kelly stake. Surfaced in **Tools → Parlay & Correlation**:
positive correlation lifts the true joint probability above the independent
product, so a book SGP priced near the independent number is +EV. The
BettingPros `/props` call also pulls `include_correlated_picks` for the sports
that support it (NFL and NBA only — requesting it elsewhere makes the API
replace the entire props list with a warning) — its correlated-leg suggestions
show on each prop's deep-dive card to seed an SGP.

### DFS pick'em lines (PrizePicks / Underdog)

BettingPros carries the DFS operators' own pick'em lines — confirmed book ids
**PrizePicks `37`, Underdog `36`** (also Betr `45`, Sleeper `63`, Dabble `53`;
see `bettingpros.DFS_BOOK_IDS` and the `data/history/raw/bp_books_*.json`
snapshots). The consensus `/props` board only shows the single best-priced book
per side, so the per-operator line lives in the `/offers` per-book breakdown
(`selections[].books[].lines[]`). `bettingpros.dfs_offer_lines()` pivots those
into one row per player+market+operator, and `pipeline._attach_dfs_lines()`
joins each prop to its PrizePicks/Underdog line plus our model probability *at
that line*. The **DFS Optimizer** prices every leg off the operator's own line
when present (the number you actually bet) — the gap between a softer DFS line
and the consensus is the edge — and falls back to the sportsbook consensus
otherwise.

## AI analyst (built-in "send to AI")

Every game card, prop deep-dive, and the Plays board carries a **🤖 Send to AI**
panel. The **free** path is primary: copy the clean markdown brief
(`app.ui.ai_brief_*`) into Claude.ai or any chatbot on your own subscription —
no API cost. When `ANTHROPIC_API_KEY` is set, an optional **✨ Analyze in-app**
button (clearly marked as a paid ~5¢ Anthropic API call) returns a grounded
read from Claude (`project547/ai.py`, Opus 4.8 with adaptive thinking) without
leaving the app.

## Dashboard layout

A left sidebar uses two-tier navigation grouped logically: **🏠 Home** (the
Command Center overview — KPI tiles for today's edges, best EV, and model
Brier/ROI/CLV, plus a top-edges table), **🔬 Research** (by sport: MLB, WNBA,
NBA, NHL, NCAAF), **🎯 Bets** (Best bets / Edge scanner / Expert consensus / DFS
optimizer), **📡 Live** (Scores), **🧰 Tools**, and **📈 Performance** (the
forward-test tracker). Picking an area reveals its pages. The top bar shows the
section title and a team/player search; each
sport view has Games (matchup cards with team logos, projected score, win
%, and the best model edge) and Props tabs. Team logos come from free CDNs
(MLB: mlbstatic by team id; WNBA/NBA/NHL: ESPN by abbreviation) with a
colored-monogram fallback when a logo is missing (`app/assets.py`).
Presentation helpers live in `app/ui.py` (streamlit-free) and are unit-tested.

## Hosting — static site on GitHub Pages

The front end is a **static site** generated by `scripts/build_static.py` from
the hourly `data/output/latest.json` and published to **GitHub Pages** (no
server, no per-view API calls). It's built and deployed by
`.github/workflows/pages.yml` on every push to `main` that touches the data or
the site code.

One-time setup: **Settings → Pages → Build and deployment → Source → “GitHub
Actions.”** After that every hourly data update republishes the site
automatically. The **Ask AI** chip is a copy-paste prompt, so the public site
needs no keys of its own; all secrets live only in the GitHub Actions
environment used by the hourly job.

> The Streamlit dashboard was retired in favour of this static site — the shared
> rendering (`app/ui.py`, `app/theme.py`, `app/assets.py`) is reused by the
> build, so `app/` stays, streamlit-free.

### Install as an app (iPhone / iPad / Android)

The site is a **PWA** — a home-screen app, no App Store. The manifest, service
worker, and icons live in `app/pwa/` and are copied into `site/` at build time;
the service worker caches the pages so the app opens instantly and works offline
(showing the last-synced slate), and refreshes when you're online.

- **iPhone / iPad (Safari):** open the site → Share → **Add to Home Screen**. It
  launches full-screen with the 360Five icon.
- **Android (Chrome):** open the site → menu → **Install app** / Add to Home Screen.

The icons are regenerated with `python scripts/make_app_icons.py` (needs Pillow;
a dev-only step — the committed PNGs are what ship).

## Extending

- **Deepening a sport**: the generic engine is intentionally simple. To
  upgrade a sport the way MLB is upgraded, add a stats client (e.g.
  nba_api, nfl_data_py) and a model module, then branch in
  `pipeline.run()` like `_run_mlb` does. Ratings → margin/total
  distributions and the entire edge/Kelly layer are already shared.
- **Game market IDs** for non-MLB sports are resolved at runtime from
  `/markets` by slug (`bettingpros.game_market_ids`). If a sport's
  moneyline/total/spread slugs differ from the candidates in
  `project547/clients/bettingpros.py`, run `scripts/discover_markets.py
  <SPORT>` and extend the candidate lists.
- **NFL week numbers**: FantasyPros NFL projections are weekly
  (`fantasypros.nfl_projections(season, week)`); wiring week inference
  into the generic props blend is the first NFL-season improvement to make.
- **Closing-line tracking**: persist `latest.json` per date (the Action
  commits history) and compare your openers to closers to measure whether
  the model beats CLV — do this before sizing up.
- **Historical data** curated from prior EdgeCash repos lives in
  `data/history/` (closing lines for 4 sports, a decade of MLB
  backfill + Statcast xstats, WNBA player logs to 2018 and Elo to 2002,
  648k graded prop projections, fitted calibration params). Load it via
  `project547/history.py`; see `data/history/README.md` for the manifest.
- **Backtesting** (`project547/backtest.py`, `scripts/run_backtest.py`):
  walk-forward (no lookahead) game backtests for MLB and WNBA graded
  against actuals and closing lines, plus a WNBA prop-distribution
  calibration check. Run `python scripts/run_backtest.py`; it writes a
  dated report to `reports/`. See the latest report for current model
  skill, calibration, and CLV — read it before sizing up.
- **Model knobs** live in `project547/config.py` (MLB) and
  `project547/sports.py` (per-sport constants).

## The data library & credit-free rebuilds

Every hourly run grows a committed library under `data/history/`:
odds snapshots (per capture, per book — last pre-game capture = closing
line), full BettingPros events (MLB lineups + park factors), the markets
catalog, every FantasyPros projection pull, player box logs, archived
projections, and graded results. Day-files older than a day are gzipped
automatically (`snapshots.compact`).

To ship a model/feature tweak between hourly pulls without burning
BettingPros/FantasyPros credits, run the **"Rebuild site"** workflow from
the Actions tab (or `python scripts/rebuild_site.py`). It re-runs the
pipeline with the paid APIs replayed from the library (`project547/
replay.py`) — free sources still fetch live — and commits a fresh
latest.json. Pure UI changes need nothing at all: Streamlit redeploys on
every push.
