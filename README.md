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
  (league scoring, HFA, volatility) live in `onesource/sports.py`.
- **Props**: BettingPros `/props` supplies every line plus their premium
  projection; FantasyPros daily projections blend in where they exist
  (NBA). Our distribution layer (Poisson for small counts, Normal for
  points/yards) converts the blended projection into P(over), then EV on
  both sides and a Kelly stake on whichever side is positive.

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

## Daily automation

`.github/workflows/daily.yml` runs the pipeline at 14:00 UTC using the
repo secrets you already configured, then commits `data/output/latest.json`.
Scheduled runs only fire on the **default branch**, so merge this branch
to enable it. You can also trigger it manually from the Actions tab
(workflow_dispatch) with an optional date.

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
