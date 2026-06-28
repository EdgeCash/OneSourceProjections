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
# CollegeFootballData (NCAAF EPA/PPA, SP+/FPI, returning production, talent,
# lines). Free key at https://collegefootballdata.com/key. See project547/clients/cfbd.py.
CFBD_API_KEY = lambda: secret("CFBD_API_KEY")  # noqa: E731

# ntfy.sh push notifications (new first-qualify DFS cards). Set NTFY_TOPIC to a
# long, private topic name and subscribe to it in the ntfy iOS app. NTFY_SERVER
# and NTFY_TOKEN are optional (self-hosted / access-token-protected topics).
NTFY_TOPIC = lambda: secret("NTFY_TOPIC")  # noqa: E731
NTFY_SERVER = lambda: secret("NTFY_SERVER", "https://ntfy.sh")  # noqa: E731
NTFY_TOKEN = lambda: secret("NTFY_TOKEN")  # noqa: E731
# Topic the Played/Skip buttons POST to and the job polls; defaults to
# "<NTFY_TOPIC>-confirm" when unset (no extra secret needed).
NTFY_CONFIRM_TOPIC = lambda: secret("NTFY_CONFIRM_TOPIC")  # noqa: E731

# The Odds API (multi-book lines). Credit-frugal defaults: us region, the
# three cheap featured markets, cached ~hourly, and a hard credit floor below
# which we stop calling so the monthly balance can never drain to zero.
# Books: "us" is the core ~10 US books; "us2" adds ESPN BET, Fanatics, Fliff,
# etc. More books => a sharper de-vigged consensus and better line shopping on
# the EDGES tab. Cost is (markets x regions) credits per sport per call, so
# us,us2 with h2h,totals = 4 credits/sport/hr; the credit floor still guards the
# account. Trim back to "us" to halve spend. Add "spreads" when EDGES needs it.
ODDS_API_REGIONS = "us,us2"
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

# Run-distribution dispersion for the MLB game simulation. Real per-team runs
# are heavily overdispersed relative to Poisson: measured var/mean ≈ 2.3 on
# 2025-26 game logs (≈2.3 even within-team, i.e. conditional on the team's own
# mean), because innings aren't independent and scoring is bursty. A raw Poisson
# (var = mean) is therefore overconfident in the tails — it mis-prices totals at
# the edges and is slightly overconfident on moneylines. We draw runs from a
# negative binomial (gamma-Poisson mixture) with var = mean × RUN_DISPERSION.
# 1.0 reduces exactly to the old Poisson behavior. Tuned on the walk-forward
# backtest (MLB 2024-26): as dispersion rises 1.0 -> 2.3 the totals-bet ROI vs
# closing climbs -9.1% -> +1.9% (win rate 44.7% -> 50.4%) and moneyline log-loss
# falls 0.6853 -> 0.6840; it then degrades past 2.3. The backtest optimum (2.3)
# coincides with the directly-measured run dispersion (var/mean 2.33), so this
# is empirically grounded, not curve-fit. (The audit suggested 1.3, which the
# data shows is far too low.)
RUN_DISPERSION = 2.3

# Weight given to FantasyPros projections when blending with our own rates.
FP_BLEND_WEIGHT = 0.5

# Betting thresholds.
MIN_EDGE = 0.02  # only surface bets with >= 2% EV edge
KELLY_FRACTION = 0.25  # quarter Kelly

# DFS first-qualify logging: a prop leg is logged (and notified) the first hour
# its model edge over the priced number clears this bar. Higher than MIN_EDGE
# because DFS multipliers carry a steep house edge — a leg needs real room.
DFS_MIN_EDGE = 0.04

# "Sharp" game play: the notify-worthy EV band. Forward-test CLV shows the
# model's REAL edge sits in MODERATE EV (2-6%: +9 to +32% avg CLV, ~75% beat
# the close), while very high model EV on these efficient markets means a stale
# line / missing info, not value (6-10%: negative CLV, losing). So we notify the
# sharp band and FLAG, not chase, the high-EV ones. Revisit as the sample grows.
SHARP_EV_MIN = 0.02   # notify at/above this EV (the curated 2-6% band)...
SHARP_EV_MAX = 0.06   # ...and at/below this (the validated sweet spot)
STALE_EV = 0.08       # at/above this, likely a stale line -> "verify", no push

# A logged DFS leg is tagged a "smash" at this edge over the de-vigged line.
DFS_SMASH_EDGE = 0.08

# Daily recap push: fired once per day on the first hourly run at/after this ET
# hour, with two numbers — overall model accuracy and personal played accuracy.
RECAP_HOUR_ET = 10

# Player props are only PULLED during these ET hours (inclusive). Props matter
# near game time and BettingPros' daily request budget is finite, so there's no
# point burning it overnight when no lines are posted. Games, snapshots and
# grading still run every hour — only the (expensive) prop calls are windowed.
PROPS_WINDOW_ET = (9, 21)  # 9am–9pm ET


def props_window_open(hour_et: int) -> bool:
    """True when prop pulls are allowed at this ET hour (24h clock)."""
    lo, hi = PROPS_WINDOW_ET
    return lo <= hour_et <= hi

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
