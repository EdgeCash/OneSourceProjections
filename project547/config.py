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
# Soccer/tennis lines move slowly and are secondary markets, so we don't need
# them hourly — refresh at most twice a day to conserve credits.
ODDS_API_SLOW_TTL = 12 * 60 * 60  # 12h (~twice a day) for soccer/tennis
ODDS_API_MIN_CREDITS = 1000  # stop calling once the account drops below this

# ---------------------------------------------------------------------------
# Model knobs. Tune these as you collect results.
# ---------------------------------------------------------------------------

# League-average runs per team per game; used as the regression prior.
LEAGUE_RUNS_PER_GAME = 4.5

# Home teams win ~53-54% of even matchups historically.
HOME_FIELD_RUNS = 0.12

# Walk-off / last-at-bat structure (audit #11). HOME_FIELD_RUNS=0.12 alone
# delivers only ~51.2% home win prob for even teams under the NB run
# distribution — the missing mechanism is that ties break asymmetrically in
# reality (home bats last knowing what it needs; home teams win extra-inning
# games ≈52% historically). So the sim's tie-break awards the extra-inning
# winner with this probability (models/game.break_ties) instead of a symmetric
# run race; 0.5 restores the old symmetric behavior.
# VALIDATED: walk-forward MLB backtest 2022-26 (run_game_backtest, starters+
# bullpen+park, 11,141 graded games): 0.50 -> 0.52 -> 0.54 moves ML log-loss
# 0.6852 -> 0.6851 -> 0.6850 and 2026 ML bet ROI 2.7% -> 3.7% -> 4.8% (~135
# bets — noise margin), totals MAE flat. Monotone but tiny in-sample, so we
# ship the literature value 0.52 rather than chase 0.54's noise.
HOME_EXTRA_WIN_P = 0.52

# Additive post-sim shift on the simulated home win prob (clamped to [0,1]),
# for the residual last-at-bat edge (9th-inning walk-offs in regulation) the
# run race can't express. Applied in models/game.simulate; 0 = off.
# VALIDATED on the same 2022-26 walk-forward sample: empirical home win rate
# is 0.529 vs the model's mean 0.518 (at HOME_EXTRA_WIN_P=0.52), and sweeping
# the shift over {0, .005, .01, .015, .02} the pooled Brier/log-loss minimum
# sits at 0.01 (Brier 0.24601 -> 0.24590, log-loss 0.68510 -> 0.68487);
# per-season optima range 0 (2023-24) to ~0.015-0.02 (2022/25/26), so 0.01 is
# the conservative pooled argmin. Totals are untouched (win-prob only).
HOME_WIN_SHIFT = 0.01

# Extra-innings scoring floor (audit P4 #9). The tie-break's per-side,
# per-frame lambda is max(mu/9, EXTRA_FRAME_RUNS). Since 2020 the ghost
# runner (automatic runner on 2nd) makes extra frames score far above a
# normal inning — roughly a run per full extra inning (~0.5/side/frame, the
# walk-off truncation of the bottom half keeps it below the raw ~1.0
# runner-on-2nd run expectancy) — so tied-game totals were understated when
# mu/9 (~0.45-0.55) was used for weak offenses. 0 restores pure mu/9.
EXTRA_FRAME_RUNS = 0.5

# How many recent team games feed the offense rating.
TEAM_FORM_GAMES = 30

# Shrinkage: weight on team's own rate vs league average (Bayesian-ish).
TEAM_RATE_WEIGHT = 0.65

# Starter is assumed to cover this share of the game before the bullpen.
STARTER_INNINGS_SHARE = 5.3 / 9.0

# Lineup-level offense (roadmap T2.2): blend a run estimate built from the nine
# posted batters' wOBA into the team-rate base. 0 = off (pure team rate); 1 =
# pure lineup. VALIDATED ON: scripts/validate_lineup_runs.py, MLB 2024, 499 games
# with posted lineups — totals MAE and moneyline Brier both improve monotonically
# as the blend rises (MAE 3.450→3.372, Brier 0.2446→0.2415 at blend 0→1). Set to
# a conservative 0.35: clearly beneficial, with headroom left because (a) only
# 2024 batter stats were available to validate (the 2023 FanGraphs pull is
# blocked here) and (b) the backtest's season-aggregate wOBA carries mild
# lookahead the live as-of feed does not. Raise it once a strict as-of wOBA feed
# and a second validation season confirm the larger blends.
LINEUP_BLEND = 0.35

# How strongly to apply park factors to expected runs (0 = off, 1 = full).
# The expected-runs math already de-biases each team's own home park, so
# full weight (1.0) is appropriate and validated best in backtests.
PARK_WEIGHT = 1.0

# Temperature effect on run scoring (outdoor games only). Warm air is less dense,
# so the ball carries — a real, well-documented effect. Measured on 2021-25
# game_context: total runs rise ~0.44 per +10F (≈0.5%/F of the ~8.9-run mean).
# Applied as a game-level multiplier on both teams' expected runs (scales the
# total, leaves the margin/win-prob untouched). TEMP_COEF is per-degree; 0 = off.
# Tuned on the walk-forward totals MAE; skipped for domes/closed roofs.
# TEMP_COEF tuned on the walk-forward backtest (MLB 2022-25, ~8k outdoor games):
# totals MAE 3.520 -> 3.511 and RMSE 4.449 -> 4.436 with near-zero bias at 0.003.
# (Lower than the raw 0.005 runs/°F regression because team form already carries
# part of the scoring environment, so the marginal per-game coefficient is
# smaller.) 0 = off.
TEMP_COEF = 0.003          # fractional run change per °F away from baseline
TEMP_BASELINE_F = 72.0     # neutral temperature (no adjustment)
TEMP_CLAMP = 0.08          # cap the total swing at ±8% (guards bad/extreme temps)

# Wind effect on run scoring (outdoor games only). Wind out to center carries
# fly balls for extra runs, wind in kills them — a real effect at open parks.
# Applied as a game-level multiplier = 1 + WIND_COEF · out_component · mph, where
# out_component ∈ [-1, +1] (+1 straight out to CF, -1 straight in, 0 crosswind).
# WIND_COEF is per (mph · unit-out); 0 = off. Tuned on the walk-forward totals MAE
# like TEMP_COEF; skipped for domes / unknown wind direction.
WIND_COEF = 0.0            # set > 0 only once the backtest shows it helps
WIND_CLAMP = 0.12          # cap the wind swing at ±12%

# Home-plate umpire tendency. The committed table (data/history/mlb_umpires.json,
# built by scripts/build_mlb_umpires.py) carries per-ump runs/K/BB indexes vs the
# league average, shrunk toward 1.0 by game count. The home-plate ump is surfaced
# as game context on every game (see umpires.for_game / pipeline), and these
# weights + clamps gate how much — if at all — that tendency also nudges the model.
#
# These default to 0 (context-only) on purpose. On the first half-season on disk
# each ump has only ~10-17 plate games, and at that sample the runs index is
# dominated by which teams/pitchers they happened to draw, not a stable zone
# effect: the k_idx↔runs_idx correlation is ≈0 where a real zone signal would be
# negative (wide zone → more strikeouts, fewer runs). Letting that move a tuned
# totals model would inject noise. So we ship the assignment + numbers now and,
# like the EPA blend, keep the model coefficient pinned to 0 until a multi-season
# table shows an out-of-sample signal — then raise these (the plumbing is wired
# and tested). Applied like temperature: RUNS is a game-level multiplier on both
# teams' expected runs (moves the total, not the margin); K multiplies the
# pitcher-strikeout environment (that path already ships behind the edge gate).
# Validated on the multi-season committed table (build_mlb_umpires.py, 20k+
# regular-season games 2016-25, ump ids matched via the retrosheet biofile).
# Out-of-sample (train 2016-22 -> test 2023-25, umps with >=40 games each side):
# K-rate tendency persists (train->test corr 0.27) but the run-environment
# tendency barely does (0.15 — runs are dominated by the offenses/pitchers, the
# zone is a small lever). So we enable a small, shrunk, clamped K coefficient and
# keep RUNS pinned to 0 (context-only) until it shows out-of-sample signal.
# K multiplies the pitcher-strikeout environment, which ships behind the edge gate.
UMPIRE_RUNS_WEIGHT = 0.0    # OFF (context-only): runs tendency doesn't hold out-of-sample
UMPIRE_RUNS_CLAMP = 0.035   # cap the total swing at ±3.5% (~±0.32 runs) when enabled
UMPIRE_K_WEIGHT = 0.35      # small: ~0.27 OOS persistence, on an already-shrunk index
UMPIRE_K_CLAMP = 0.06       # cap the strikeout swing at ±6%

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

# ---------------------------------------------------------------------------
# Demonstrated-edge gate (project547/edge_gate.py). The EV band above says WHICH
# disagreements to consider; the gate says WHICH MARKETS we've actually proven an
# edge in. A market must earn curation via realized closing-line value (CLV) — a
# 2-6% edge on a market we don't beat is noise. Measured on a rolling window of
# the graded ledger, per (sport, market). Asymmetric: easy-ish to clear, hard to
# gate off (so a market isn't killed on noise). Tune as the sample grows.
GATE_WINDOW_DAYS = 180     # rolling window of graded results the gate reads
GATE_CLEAR_MIN = 30        # CLV-graded bets needed to CLEAR a market...
GATE_CLV_FLOOR = 0.0       # ...with avg CLV at/above this (beating the close)
GATE_OFF_MIN = 60          # more evidence required to GATE a market off...
GATE_OFF_CLV = -0.01       # ...and avg CLV at/below this (clearly losing to close)
GATE_PROBATION_STAKE = 0.5  # Kelly multiplier for unproven (probation) markets

# Curation seed (docs/CURATION_DESIGN.md step 2): a walk-forward production-mode
# backtest scores the *current* model over historical slates and writes per-market
# CLV to data/history/curation_seed.json, so conviction/gate have a prior from day
# one instead of waiting for the live ledger to accrue. OFF by default — the seed's
# CLV comes from backfill closing consensus, not the live BettingPros close, so it's
# a discounted prior the owner opts into after reviewing the numbers. When on, the
# seed only supplements markets whose *live* CLV sample is still thin (< GATE_CLEAR_MIN);
# once real data clears that bar the seed is ignored, so it self-retires.
CURATION_SEED_ENABLED = True
CURATION_SEED_PATH = REPO_ROOT / "data" / "history" / "curation_seed.json"

# Slate-level stake shaping (docs/CURATION_DESIGN.md Component 3), applied after
# per-market gate scaling. Both OFF by default (identity) — turn on after review.
#   SLATE_CORR: assumed common pairwise correlation among same-game legs. Each
#     leg in a k-leg game is scaled by 1/(1+(k-1)*SLATE_CORR) — the equicorrelation
#     Kelly haircut. 0.0 = no haircut. A practitioner value is ~0.25-0.35.
#   SLATE_MAX_EXPOSURE: cap on the slate's total suggested stake as a fraction of
#     bankroll; over it, every stake scales down proportionally. None = no cap.
SLATE_CORR = 0.0
SLATE_MAX_EXPOSURE = None

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
#     Re-derived after the 2026 model upgrades (consistent-margin + negative-
#     binomial runs): the MLB betting sweep still peaks in the 0.5-0.65 band
#     (ML ROI 3.5% -> 15.4% -> 19.9% across shrink 0.0/0.5/0.65, but 0.5->0.65
#     is a ~145-bet noise margin), so 0.5 is unchanged. Note the *calibration*-
#     optimal blend is ~1.0 (MLB moneylines are efficient — the de-vigged close
#     out-predicts the model), but shrink is a bet-SELECTION knob, not a
#     calibration one: at 1.0 nothing clears MIN_EDGE. 0.5 keeps only the model
#     disagreements large enough to survive, and those carry the positive ROI.
MARKET_SHRINK = 0.5
#   A two-way market's raw implied probs must sum within this band to count
#   as a coherent quote; outside it the prices are stale/mismatched/alt-line.
VIG_SUM_MIN = 0.98
VIG_SUM_MAX = 1.30

# Post-hoc probability calibration (project547.calibrate). When on, the model's
# raw probability is passed through the isotonic map fit by scripts/fit_calibration.py
# before EV/shrink. Default OFF so wiring is inert until the maps are validated;
# flip via env APPLY_CALIBRATION=1. See docs/MODEL_REPAIR.md.
APPLY_CALIBRATION = os.environ.get("APPLY_CALIBRATION", "").strip().lower() in (
    "1", "true", "yes", "on")

# Market-anchored published projection (roadmap T3.1 / docs/CURATION_DESIGN.md
# Component 4). The most accurate number we can *publish* today is one blended
# toward the market consensus (the T0.1 backtest shows the raw model trails the
# de-vigged close on every game market). This knob anchors ONLY the published
# *_pub columns toward the market; the raw model columns (home_win_prob,
# proj_total, home_exp/away_exp, margin) are left untouched so edge detection —
# which must measure the model's deviation FROM the market — is unaffected
# (anchoring the edge input would erase the edge).
#
# Keys are (sport, market) tuples — market in {"moneyline", "total", "spread"};
# values in [0, 1] weight the market anchor vs the model (0 = pure model, 1 =
# pure market). Empty dict → every weight is 0 → every *_pub column exactly
# equals its raw column → nothing changes live. Raise a weight only after the
# open→close CLV validation cycle (T3.1) earns it, per-market: efficient markets
# stay ~0, props/secondary sports may get more.
#
# scripts/validate_anchor.py swept alpha per (sport, market) against outcomes in
# the production-mode backtest. Accuracy improved *monotonically* toward the
# market on every one (moneyline Brier and total MAE), confirming T0.1 — but the
# backtest's market proxy is the CLOSING line, so that gain is an UPPER BOUND
# (production anchors to the softer current line) and a published number equal to
# the market has zero deviation → zero edge. The conservative recommendation
# (capture ~half the gain, keep the model's deviation) came out at 0.5 for every
# market measured. Left OFF pending review; to enable, set e.g.:
#   PROJECTION_ANCHOR = {
#       ("MLB", "moneyline"): 0.5, ("MLB", "total"): 0.5,
#       ("NBA", "moneyline"): 0.5, ("NBA", "total"): 0.5,
#       ("NFL", "moneyline"): 0.5, ("NFL", "total"): 0.5,
#       ("NHL", "moneyline"): 0.5, ("NHL", "total"): 0.5,
#   }
PROJECTION_ANCHOR: dict = {}


def projection_anchor(sport, market) -> float:
    """Published-projection anchor weight for a (sport, market), 0 when unset."""
    return float(PROJECTION_ANCHOR.get((sport, market), 0.0))


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
