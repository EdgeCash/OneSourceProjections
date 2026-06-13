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
    # how the off/def ratings combine into an expected score:
    #   "additive"       -> (team offense + opp defense) / 2   (midpoint)
    #   "multiplicative" -> league × (off/league) × (oppDef/league)  (log5-for-
    #                       points: strong O vs weak D scores above either mean)
    score_method: str = "additive"


SPORTS: dict[str, Sport] = {
    "MLB": Sport(
        key="MLB", espn_path=None, model="poisson",
        league_ppg=4.5, hfa=0.12, sigma_margin=0.0, sigma_total=0.0,
        in_season_months=(3, 4, 5, 6, 7, 8, 9, 10, 11), form_days=75,
        fp_projections="daily",
    ),
    "WNBA": Sport(
        key="WNBA", espn_path="basketball/wnba", model="normal",
        league_ppg=82.0, hfa=2.5, sigma_margin=11.5, sigma_total=15.0,
        in_season_months=(5, 6, 7, 8, 9, 10), form_days=45,
        elo_blend=0.65,  # 0.35 off/def model + 0.65 Elo (backtested)
        score_method="multiplicative",
    ),
    "NBA": Sport(
        key="NBA", espn_path="basketball/nba", model="normal",
        league_ppg=114.0, hfa=2.5, sigma_margin=12.5, sigma_total=19.0,
        in_season_months=(10, 11, 12, 1, 2, 3, 4, 5, 6), form_days=45,
        fp_projections="daily", score_method="multiplicative",
    ),
    "NFL": Sport(
        key="NFL", espn_path="football/nfl", model="normal",
        league_ppg=22.5, hfa=1.8, sigma_margin=13.5, sigma_total=13.5,
        in_season_months=(9, 10, 11, 12, 1, 2), form_days=140,
        fp_projections="weekly", score_method="multiplicative",
    ),
    "NCAAF": Sport(
        key="NCAAF", espn_path="football/college-football", model="normal",
        league_ppg=28.0, hfa=2.7, sigma_margin=16.0, sigma_total=16.5,
        in_season_months=(8, 9, 10, 11, 12, 1), form_days=140,
        espn_params={"groups": 80, "limit": 400},  # FBS only
        score_method="multiplicative",
    ),
    "NHL": Sport(
        key="NHL", espn_path="hockey/nhl", model="poisson",
        league_ppg=3.0, hfa=0.15, sigma_margin=0.0, sigma_total=0.0,
        in_season_months=(10, 11, 12, 1, 2, 3, 4, 5, 6), form_days=45,
        score_method="multiplicative",
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
