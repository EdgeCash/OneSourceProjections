# OneSource Projections

Personal multi-sport betting model — **MLB, WNBA, NBA, NFL, NCAAF, NHL** —
projecting games (moneyline / total / spread) and player props, with edges
computed against BettingPros market lines and a private Streamlit dashboard.

**Personal use only. Not financial advice. Bet responsibly.**

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

The pipeline runs every sport that's in season (`onesource/sports.py`
defines the calendar; override with `--sports`).

### MLB (the deep model)

- **Game model** (`onesource/models/game.py`): recent team scoring rate
  shrunk toward league average; opposing **starter** quality applied over
  the innings starters cover and opposing **bullpen** quality over the
  rest (each as FIP / league FIP); **park factors** applied to the venue
  after de-biasing each team's own home park; plus home field. 20k-draw
  Poisson Monte Carlo (ties resolved as extra innings) → win prob,
  over/under probs, run-line cover probs. Park factors are derived
  empirically (`scripts/compute_park_factors.py` →
  `data/history/park_factors.json`, loaded via `onesource/parks.py`).
  Backtested 2024–2026, each component improves the model monotonically
  (Brier 0.2483→0.2463, total-runs MAE 3.60→3.55, favorite hit-rate
  0.540→0.552); open→close CLV is +12.8% moneyline ROI at opening prices.
- **Prop models** (`onesource/models/props.py`): Poisson for Ks and total
  bases, binomial for hits, per-PA rate for HRs. Our Statcast-informed
  rates are blended 50/50 with FantasyPros projections when available.

### WNBA / NBA / NFL / NCAAF / NHL (the generic engine)

- **Game model** (`onesource/models/generic.py`): offensive/defensive
  ratings from recent final scores (ESPN), shrunk toward league average,
  plus home advantage. Basketball/football use a Normal margin/total
  model; NHL uses the same Poisson simulation as MLB. Per-sport constants
  (league scoring, HFA, volatility) live in `onesource/sports.py`. For
  sports with `elo_blend > 0`, an Elo rating system
  (`onesource/models/elo.py`, maintained live from results) is blended
  into the moneyline win probability. WNBA uses 0.35 off/def + 0.65 Elo,
  which backtests to Brier 0.227 → **0.215** (favorite hit-rate
  0.62 → 0.67), well-calibrated across a 0.2–0.9 range.
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
unit and quarter-Kelly stake, for every game market and prop.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in your keys
python scripts/run_daily.py                       # all in-season sports
python scripts/run_daily.py --sports WNBA,MLB     # or pick specific ones
streamlit run app/dashboard.py                    # view them
```

Required secrets (env vars, `.env`, or Streamlit secrets):

| Name | Purpose |
|---|---|
| `FANTASYPROS_API_KEY` | FantasyPros public API key (`x-api-key` header) |
| `BP_PARTNER_KEY` | BettingPros partner key, sent as `x-api-key` on every call |
| `BP_USER`, `BP_USER_KEY` | BettingPros premium tier: sent as `auth=user&user=…&key=…` query params to unlock projections/EV/recommended sides |
| `APP_PASSWORD` | Dashboard password gate |
| `ANTHROPIC_API_KEY` | Optional — enables the in-app **AI analyst** (`✨ Analyze`). Without it the dashboard still offers copy-for-AI briefs. |
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
   `onesource/config.py` to match; the defaults are placeholders. The
   flatteners in `onesource/clients/bettingpros.py` pull fields
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
(`onesource/playerlogs.py`) and shown as a red→green gradient on the Props
view. Selecting a prop draws a bar chart of the player's recent games
against the line (green = over, red = under). Logs come from the imported
backfill plus a forward store the hourly job appends from MLB boxscores, so
the splits stay current as the season passes the backfill cutoff. (WNBA
forward log ingestion is the next addition; its splits are current through
the import for now.)

## Game research cards

Each game has a full matchup breakdown (`onesource/teamstats.py` +
`app/ui.research_card_html`): the team's offense compared to the opponent's
matching defense across Season/L10/L5 with **league ranks** and a star
**advantage** flag where the offense out-ranks the defense it faces, plus
model gauges (moneyline / total with PLAY/PASS) and — for MLB — game
trends (NRFI%, F5 win%, RL cover%, Over%, Pythagorean). Team identity is
resolved through `onesource/teams.py` so full names, cities, and
abbreviations all join. Stats are derived from our box-score logs; a few
reference stats we don't capture (e.g. WNBA paint points, fast break) are
omitted. Generate a static HTML preview of all the graphics with
`python scripts/make_preview.py --sport WNBA`.

## Multi-book edge scanner (the sharp layer)

`onesource/edge.py` adds what the elite tools (OddsJam, Unabated) are built on:
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

`onesource/experts.py` builds a multi-source consensus per prop from three
*independent* reads: **our model** (`model_over_prob` → a lean), **BettingPros'
expert recommendation** (`bp_recommended_side` + their `bp_bet_rating` ★
confidence — premium fields populated by the `BP_USER` auth), and the **public**
(`pick_pct_over` pick distribution). The **Experts** tab ranks props by how many
sources agree (✅ when all do), with a top-bar search by player/team/market —
so you can quickly check where the experts, the public, and the model line up.
Pure over the published slate, unit-tested in `tests/test_experts.py`.

## SGP correlation finder

`onesource/sgp.py` prices same-game parlays through the Gaussian copula
(`onesource.calculators`) using correlation **priors** for common leg
relationships (`CORRELATION_PRESETS`). Given each leg's win probability and a
correlation, `price_sgp` returns the correlation-adjusted joint probability, the
fair vs naive-independent prices, the "lift", and — with the book's quoted SGP
price — the EV and ¼-Kelly stake. Surfaced in **Tools → Parlay & Correlation**:
positive correlation lifts the true joint probability above the independent
product, so a book SGP priced near the independent number is +EV. The
BettingPros `/props` call also pulls `include_correlated_picks` (no extra
request, so the 5k/day budget is untouched) — its correlated-leg suggestions
show on each prop's deep-dive card to seed an SGP.

## AI analyst (built-in "send to AI")

Every game card, prop deep-dive, and the Plays board carries a **🤖 Send to AI**
panel. The **free** path is primary: copy the clean markdown brief
(`app.ui.ai_brief_*`) into Claude.ai or any chatbot on your own subscription —
no API cost. When `ANTHROPIC_API_KEY` is set, an optional **✨ Analyze in-app**
button (clearly marked as a paid ~5¢ Anthropic API call) returns a grounded
read from Claude (`onesource/ai.py`, Opus 4.8 with adaptive thinking) without
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
Presentation helpers live in `app/ui.py`; both are unit-tested and the whole
app is render-tested via Streamlit's AppTest harness.

## Hosting the dashboard privately

Recommended: **Streamlit Community Cloud** (free) with both layers:

1. Deploy the app from this **private** repo at share.streamlit.io, main
   file `app/dashboard.py`.
2. In app settings → **Sharing**, set the app to *private* — only viewers
   you invite by email can even load it.
3. In app settings → **Secrets**, paste:

   ```toml
   APP_PASSWORD = "something-long-and-random"
   FANTASYPROS_API_KEY = "..."
   BP_PARTNER_KEY = "..."
   BP_USER = "..."
   BP_USER_KEY = "..."
   ```

The in-app password gate (`app/auth.py`, constant-time compare) means that
even if the URL leaks or sharing is misconfigured, a visitor sees only a
password box. New commits (including the daily data commit from the
Action) auto-redeploy the app, so the dashboard refreshes itself every day
without keeping keys anywhere but GitHub/Streamlit secrets.

Alternatives if you outgrow it: Fly.io or Render with the same env vars
(`streamlit run app/dashboard.py --server.port $PORT`), or run it on a
home box behind Tailscale for a fully invisible deployment.

## Extending

- **Deepening a sport**: the generic engine is intentionally simple. To
  upgrade a sport the way MLB is upgraded, add a stats client (e.g.
  nba_api, nfl_data_py) and a model module, then branch in
  `pipeline.run()` like `_run_mlb` does. Ratings → margin/total
  distributions and the entire edge/Kelly layer are already shared.
- **Game market IDs** for non-MLB sports are resolved at runtime from
  `/markets` by slug (`bettingpros.game_market_ids`). If a sport's
  moneyline/total/spread slugs differ from the candidates in
  `onesource/clients/bettingpros.py`, run `scripts/discover_markets.py
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
  `onesource/history.py`; see `data/history/README.md` for the manifest.
- **Backtesting** (`onesource/backtest.py`, `scripts/run_backtest.py`):
  walk-forward (no lookahead) game backtests for MLB and WNBA graded
  against actuals and closing lines, plus a WNBA prop-distribution
  calibration check. Run `python scripts/run_backtest.py`; it writes a
  dated report to `reports/`. See the latest report for current model
  skill, calibration, and CLV — read it before sizing up.
- **Model knobs** live in `onesource/config.py` (MLB) and
  `onesource/sports.py` (per-sport constants).

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
pipeline with the paid APIs replayed from the library (`onesource/
replay.py`) — free sources still fetch live — and commits a fresh
latest.json. Pure UI changes need nothing at all: Streamlit redeploys on
every push.
