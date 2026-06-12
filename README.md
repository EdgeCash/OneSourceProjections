# OneSource Projections

Personal MLB betting model: game projections (moneyline / total / run line)
and player props (pitcher Ks, batter hits / total bases / home runs), with
edges computed against BettingPros market lines and a private Streamlit
dashboard.

**Personal use only. Not financial advice. Bet responsibly.**

## How it works

```
MLB StatsAPI ──► slate, probables, lineups, team form ─┐
pybaseball   ──► FanGraphs rates + Statcast xBA/xSLG ──┤
FantasyPros  ──► daily player projections ─────────────┼──► models ──► P(outcomes)
                                                       │                  │
BettingPros  ──► lines & best prices ──────────────────┘    de-vig, EV, ¼-Kelly
                                                                          │
                                              data/output/latest.json ◄───┘
                                                          │
                                              Streamlit dashboard (password-gated)
```

- **Game model** (`onesource/models/game.py`): recent team scoring rate
  shrunk toward league average, adjusted for the opposing starter's xFIP
  over the innings starters cover, plus home field. 20k-draw Poisson Monte
  Carlo (ties resolved as extra innings) → win prob, over/under probs,
  run-line cover probs.
- **Prop models** (`onesource/models/props.py`): Poisson for Ks and total
  bases, binomial for hits, per-PA rate for HRs. Our Statcast-informed
  rates are blended 50/50 with FantasyPros projections when available.
- **Edges** (`onesource/pipeline.py`): model probability vs the best
  available price from BettingPros → EV per unit and quarter-Kelly stake.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in your keys
python scripts/run_daily.py            # build today's projections
streamlit run app/dashboard.py         # view them
```

Required secrets (env vars, `.env`, or Streamlit secrets):

| Name | Purpose |
|---|---|
| `FANTASYPROS_API_KEY` | FantasyPros projections |
| `BP_PARTNER_KEY` | BettingPros API key (`x-api-key`) |
| `BP_USER`, `BP_USER_KEY` | BettingPros account credentials |
| `APP_PASSWORD` | Dashboard password gate |

### First-run checklist

1. `python scripts/discover_markets.py MLB` — prints your account's market
   IDs. If they differ from `BP_MARKET_IDS` in `onesource/config.py`,
   update that dict. (Market IDs vary by partner tier; the offer-flattening
   in `onesource/clients/bettingpros.py` is also easy to tweak if your
   payload shape differs.)
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

- **Other sports**: the BettingPros/FantasyPros clients take a `sport`
  parameter already; add an NFL/NBA slate source and a model module, and
  reuse `onesource/odds.py` and the edge pipeline as-is.
- **Closing-line tracking**: persist `latest.json` per date (the Action
  commits history) and compare your openers to closers to measure whether
  the model beats CLV — do this before sizing up.
- **Model knobs** live at the bottom of `onesource/config.py`.
