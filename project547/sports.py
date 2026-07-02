"""Sport registry. Each entry parameterizes the generic game model and
tells the pipeline where to get slates and what BettingPros sport code to
use. MLB has its own richer pipeline (Statcast et al.); everything else
runs through the generic engine.

Tuning notes: hfa is in points (or goals); sigma_margin/sigma_total are
empirical single-game standard deviations — the most important knobs for
converting projections into probabilities.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class Sport:
    key: str                 # our identifier and BettingPros sport code
    espn_path: str | None    # ESPN scoreboard path (None = uses own client)
    model: str               # "poisson" (low scoring) or "normal"
    league_ppg: float        # league-average points/goals per team per game
    hfa: float               # home advantage in points/goals
    sigma_margin: float      # stdev of final margin (normal model only)
    sigma_total: float       # stdev of combined score (normal model only)
    in_season_months: tuple[int, ...]
    form_days: int           # lookback window for team ratings
    fp_projections: str | None = None   # FantasyPros projections support
    espn_params: dict = field(default_factory=dict)
    elo_blend: float = 0.0   # weight on Elo win prob vs the off/def model (0 = off)
    # Per-sport Elo configuration (used when elo_blend > 0). Defaults match the
    # WNBA-tuned EloConfig; other leagues set league-appropriate values below.
    elo_k: float = 16.0          # update step (fewer games -> larger k)
    elo_home_edge: float = 50.0  # home advantage in Elo points
    elo_regress: float = 0.25    # between-season pull back toward the mean
    # how the off/def ratings combine into an expected score:
    #   "additive"       -> (team offense + opp defense) / 2   (midpoint)
    #   "multiplicative" -> league × (off/league) × (oppDef/league)  (log5-for-
    #                       points: strong O vs weak D scores above either mean)
    score_method: str = "additive"
    # points of home-margin edge per rest-day differential (home_rest -
    # away_rest, each capped at 14). 0 = off. Tuned on the game backtest;
    # only set for normal-model sports where it's been measured.
    rest_coeff: float = 0.0
    # opponent-adjust (strength-of-schedule) the off/def ratings. Enabled per
    # sport once the backtest shows it helps.
    opponent_adjust: bool = False
    # weight on the starting-QB value deviation (team points) -> margin points,
    # to capture backups starting (injury). MEASURED on the 2018-24 NFL backtest:
    # a backward-looking team-points proxy nudges calibration but *hurts* CLV
    # (the market prices QB news sharply), so left at 0 pending a real-time
    # QB/depth-chart signal that can actually beat the close. Capability kept.
    qb_coeff: float = 0.0
    # weight on the opponent-adjusted EPA margin (project547/epa.py) vs the
    # points-based off/def margin. 0 = off. Stays 0 until scripts/validate_epa.py
    # shows EPA ratings beat the points model walk-forward (roadmap Stage 1);
    # needs live play-by-play wired into the slate before it can run in prod.
    epa_blend: float = 0.0
    # weight on the de-vigged market consensus vs the model when computing EV on
    # two-way markets (the old global config.MARKET_SHRINK, now per-sport). 0 =
    # pure model, 1 = pure market. Higher for efficient markets where the model's
    # disagreements with the close are mostly noise. Defaults to the historical
    # global (0.5); override per sport from the walk-forward backtest.
    market_shrink: float = 0.5


SPORTS: dict[str, Sport] = {
    "MLB": Sport(
        key="MLB", espn_path=None, model="poisson",
        league_ppg=4.5, hfa=0.12, sigma_margin=0.0, sigma_total=0.0,
        in_season_months=(3, 4, 5, 6, 7, 8, 9, 10, 11), form_days=75,
        fp_projections="daily",
        # MLB moneylines/totals are efficient; the walk-forward backtest shows
        # closing-line ROI rises monotonically with shrink (0.5->12%, 0.65->14%,
        # 0.8->17% on the 2024-26 matched sample) as bet volume falls onto the
        # model's largest, least-noisy disagreements. 0.65 sits in the validated
        # 0.5-0.65 band. Other sports stay at the 0.5 default until their own
        # backtests justify a move.
        market_shrink=0.65,
    ),
    "WNBA": Sport(
        key="WNBA", espn_path="basketball/wnba", model="normal",
        # sigma_margin/sigma_total MEASURED from walk-forward residuals (2021-26,
        # 1312 games): margin-prediction error SD 12.7 and total-prediction error
        # SD 17.0. The prior 11.5/15.0 were too tight -> the normal model was
        # overconfident on spreads/moneylines and totals; matching sigma to the
        # measured error SD calibrates both.
        league_ppg=82.0, hfa=2.5, sigma_margin=12.7, sigma_total=17.0,
        in_season_months=(5, 6, 7, 8, 9, 10), form_days=45,
        elo_blend=0.65,  # 0.35 off/def model + 0.65 Elo (backtested)
        # season_regress 0.5 (was 0.25): the k×regress sweep improves Brier at
        # every k with heavier between-season regression (short WNBA season), and
        # 0.5 matches 538's WNBA setting. NOTE: k stays 16 — the sweep shows
        # raising k toward 538's 28-32 HURTS us (Brier 0.2152 -> 0.2182 at k=32).
        elo_regress=0.5,
        score_method="multiplicative",
        # opponent_adjust and rest were tested per the model audit (2021-26
        # backtest): opponent_adjust is neutral (ML Brier 0.2162 either way) and
        # any rest_coeff > 0 hurts (0.2162 -> 0.2180 at 0.5). Left off — Elo
        # already carries strength-of-schedule for WNBA.
    ),
    "NBA": Sport(
        key="NBA", espn_path="basketball/nba", model="normal",
        league_ppg=114.0, hfa=2.5, sigma_margin=12.5, sigma_total=19.0,
        in_season_months=(10, 11, 12, 1, 2, 3, 4, 5, 6), form_days=45,
        fp_projections="daily", score_method="multiplicative",
        # Elo primed for season openers (carries prior-season strength so the
        # first weeks aren't coin flips). Mirrors WNBA's strong basketball
        # result; validate/tune via Performance -> Model vs market once games run.
        elo_blend=0.60, elo_k=20.0, elo_home_edge=65.0, elo_regress=0.25,
        # rest-days edge (back-to-backs): +cal & +CLV on the 2022-24 backtest.
        rest_coeff=0.5,
        # strength-of-schedule helps NBA on the 2022-24 backtest (logloss/CLV/ROI).
        opponent_adjust=True,
    ),
    "NFL": Sport(
        key="NFL", espn_path="football/nfl", model="normal",
        # sigma_margin 13.5 -> 16.0 and elo_blend 0.50 -> 0.60 from the 2019-2024
        # game backtest (nflverse closing lines): the off/def model was
        # overconfident at the tails, so this de-biases win-prob calibration
        # (log-loss 0.6385 -> 0.6372, Brier 0.2233 -> 0.2228). Re-tune as data grows.
        league_ppg=22.5, hfa=1.8, sigma_margin=16.0, sigma_total=13.5,
        in_season_months=(9, 10, 11, 12, 1, 2), form_days=140,
        fp_projections="weekly", score_method="multiplicative",
        # Few games/season -> larger k and a heavier between-season regression.
        elo_blend=0.60, elo_k=20.0, elo_home_edge=48.0, elo_regress=0.33,
        # rest edge (bye weeks / short weeks): +cal & +CLV on the 2021-24 backtest.
        rest_coeff=0.5,
    ),
    "NCAAF": Sport(
        # league_ppg recentred 28.0 -> 27.0: the modern era (2021-25 committed
        # backfill, n=1110) averages ~26.4 pts/team (mean total 52.8); 28.0 was
        # anchored to older, higher-scoring seasons and systematically over-
        # projected totals ~2-3 pts (model 56.0 vs actual ~53). sigma_total 16.5
        # is adequate (backtest total residual RMSE 15.5). NCAAF has no committed
        # closing lines, so this is a projection-accuracy fix, not a betting claim.
        key="NCAAF", espn_path="football/college-football", model="normal",
        league_ppg=27.0, hfa=2.7, sigma_margin=16.0, sigma_total=16.5,
        in_season_months=(8, 9, 10, 11, 12, 1), form_days=140,
        espn_params={"groups": 80, "limit": 400},  # FBS only
        score_method="multiplicative",
        # Heavy roster turnover -> regress harder toward the mean each season.
        elo_blend=0.50, elo_k=22.0, elo_home_edge=65.0, elo_regress=0.45,
    ),
    "ATP": Sport(
        # Player-vs-player: the team fields below are unused (the tennis path uses
        # a player-Elo model, models/tennis). Kept in the registry so the slate/
        # results plumbing and active_sports work. Year-round tour.
        key="ATP", espn_path="tennis/atp", model="normal",
        league_ppg=0.0, hfa=0.0, sigma_margin=0.0, sigma_total=0.0,
        in_season_months=(1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11), form_days=540,
    ),
    "MLS": Sport(
        key="MLS", espn_path="soccer/usa.1", model="poisson",
        # Goals per team per game (MLS runs ~2.9 total). hfa in goals — home
        # edge in football is worth ~0.3-0.4 goals. Season Feb-Nov (+ Dec
        # playoffs). Longer form window: teams play ~1-2x/week so 120 days keeps
        # a usable rating sample. Multiplicative (log5) scoring so a strong attack
        # vs a leaky defence projects above either mean. 1X2/draw comes from the
        # soccer scoreline model (models/soccer), not the 2-way generic engine.
        league_ppg=1.45, hfa=0.32, sigma_margin=0.0, sigma_total=0.0,
        in_season_months=(2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12), form_days=120,
        score_method="multiplicative",
    ),
    "NHL": Sport(
        key="NHL", espn_path="hockey/nhl", model="poisson",
        league_ppg=3.0, hfa=0.15, sigma_margin=0.0, sigma_total=0.0,
        in_season_months=(10, 11, 12, 1, 2, 3, 4, 5, 6), form_days=45,
        score_method="multiplicative",
        # Low-event sport -> small k (single results carry little signal).
        elo_blend=0.50, elo_k=6.0, elo_home_edge=50.0, elo_regress=0.30,
        # strength-of-schedule helps NHL on the 2022-24 backtest (logloss/CLV/ROI).
        opponent_adjust=True,
    ),
}


def in_season(sport_key: str, date: str) -> bool:
    month = int(date.split("-")[1])
    return month in SPORTS[sport_key].in_season_months


def active_sports(date: str) -> list[str]:
    return [k for k in SPORTS if in_season(k, date)]


def _game_start_et(ts) -> datetime | None:
    """Parse a game_time (UTC ISO) into an ET-aware datetime."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ET)
    return dt.astimezone(ET)


def default_slate_date(dates: list[str], slates: dict,
                       now_et: datetime | None = None,
                       live_hours: float = 4.0) -> str | None:
    """Which slate to show by default.

    Stays on *today* while today still has games that are upcoming or
    recently underway (within ``live_hours`` of the last first pitch/tip),
    then rolls forward to the next date. Anchored to Eastern time so the app
    doesn't flip to tomorrow's slate at the start of the day — or, on a UTC
    host, jump a day early in the evening.
    """
    if not dates:
        return None
    now_et = now_et or datetime.now(ET)
    today = now_et.date().isoformat()
    later = sorted(d for d in dates if d > today)
    nxt = later[0] if later else None
    if today not in dates:
        past = sorted((d for d in dates if d <= today), reverse=True)
        return past[0] if past else sorted(dates)[0]
    starts = [t for blob in (slates.get(today) or {}).values()
              for g in (blob.get("games") or [])
              if (t := _game_start_et(g.get("game_time"))) is not None]
    if not starts:
        return nxt or today
    if now_et < max(starts) + timedelta(hours=live_hours):
        return today
    return nxt or today
