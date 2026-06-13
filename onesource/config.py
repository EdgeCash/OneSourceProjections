"""Central configuration. Secrets come from env vars first, then Streamlit
secrets when running inside the dashboard, so the same code works in GitHub
Actions, locally with a .env file, and on Streamlit Cloud."""

import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = REPO_ROOT / "data" / "cache"
OUTPUT_DIR = REPO_ROOT / "data" / "output"


def secret(name: str, default: str | None = None) -> str | None:
    val = os.environ.get(name)
    if val:
        return val
    try:
        import streamlit as st

        return st.secrets.get(name, default)
    except Exception:
        return default


FANTASYPROS_API_KEY = lambda: secret("FANTASYPROS_API_KEY")  # noqa: E731
BP_PARTNER_KEY = lambda: secret("BP_PARTNER_KEY")  # noqa: E731
BP_USER = lambda: secret("BP_USER")  # noqa: E731
BP_USER_KEY = lambda: secret("BP_USER_KEY")  # noqa: E731
APP_PASSWORD = lambda: secret("APP_PASSWORD")  # noqa: E731
THE_ODDS_API_KEY = lambda: secret("THE_ODDS_API_KEY")  # noqa: E731

# The Odds API (multi-book lines). Credit-frugal defaults: us region, the
# three cheap featured markets, cached ~hourly, and a hard credit floor below
# which we stop calling so the monthly balance can never drain to zero.
# Only the markets we actually consume (CLV + grading use moneyline + totals);
# 2 credits per sport per call. Add "spreads" when line-shopping needs it.
ODDS_API_REGIONS = "us"
ODDS_API_MARKETS = "h2h,totals"
ODDS_API_TTL = 3300  # seconds (~55 min) — at most one spend per sport per hour
ODDS_API_MIN_CREDITS = 1000  # stop calling once the account drops below this

# ---------------------------------------------------------------------------
# Model knobs. Tune these as you collect results.
# ---------------------------------------------------------------------------

# League-average runs per team per game; used as the regression prior.
LEAGUE_RUNS_PER_GAME = 4.5

# Home teams win ~53-54% of even matchups historically.
HOME_FIELD_RUNS = 0.12

# How many recent team games feed the offense rating.
TEAM_FORM_GAMES = 30

# Shrinkage: weight on team's own rate vs league average (Bayesian-ish).
TEAM_RATE_WEIGHT = 0.65

# Starter is assumed to cover this share of the game before the bullpen.
STARTER_INNINGS_SHARE = 5.3 / 9.0

# How strongly to apply park factors to expected runs (0 = off, 1 = full).
# The expected-runs math already de-biases each team's own home park, so
# full weight (1.0) is appropriate and validated best in backtests.
PARK_WEIGHT = 1.0

# Monte Carlo draws for the game simulation.
SIM_DRAWS = 20_000

# Weight given to FantasyPros projections when blending with our own rates.
FP_BLEND_WEIGHT = 0.5

# Betting thresholds.
MIN_EDGE = 0.02  # only surface bets with >= 2% EV edge
KELLY_FRACTION = 0.25  # quarter Kelly

# Market-blend / price-sanity knobs. The raw model finds far too many fat
# edges (a sign of over-confidence + stale price inputs, not alpha), so before
# computing EV we (1) reject incoherent two-way prices and (2) shrink the
# model probability toward the de-vigged market consensus.
#   MARKET_SHRINK: weight on the market's fair prob vs the model (0 = pure
#     model, 1 = pure market). 0.5 roughly halved a losing backtest's bet
#     volume and flipped moneyline ROI positive; tune via run_backtest.
MARKET_SHRINK = 0.5
#   A two-way market's raw implied probs must sum within this band to count
#   as a coherent quote; outside it the prices are stale/mismatched/alt-line.
VIG_SUM_MIN = 0.98
VIG_SUM_MAX = 1.30

# BettingPros market ids vary by sport/account tier. These are sensible
# defaults for MLB; run `python scripts/discover_markets.py` once with your
# keys to print the live list and adjust here if needed.
BP_MARKET_IDS = {
    "moneyline": 1,
    "spread": 3,  # run line
    "total": 2,
    "pitcher_strikeouts": 285,
    "batter_hits": 287,
    "batter_total_bases": 288,
    "batter_home_runs": 286,
}
