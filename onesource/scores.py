"""Unified live scoreboard + box scores across sports, so results can be
followed inside the app instead of a second tab.

Free sources, already used elsewhere: ESPN for WNBA/NBA/NHL/NCAAF (and any
other ESPN league), MLB StatsAPI for baseball. Each returns a uniform game
shape: {sport, game_id, state (pre/in/post), detail, home/away: {team,
abbrev, logo, score, record}}.
"""

from __future__ import annotations

import logging

from .clients import espn, mlb_statsapi
from .sports import SPORTS, active_sports

log = logging.getLogger(__name__)


def _sport_scoreboard(sport: str, date: str) -> list[dict]:
    try:
        if sport == "MLB":
            return mlb_statsapi.scoreboard(date)
        if SPORTS.get(sport) and SPORTS[sport].espn_path:
            return espn.scoreboard(sport, date)
    except Exception as e:
        log.warning("scoreboard %s %s failed: %s", sport, date, e)
    return []


def live_scoreboard(date: str, sports: list[str] | None = None) -> list[dict]:
    """Every game across the in-season sports for a date, in-progress first."""
    sports = sports or [s for s in SPORTS if s in active_sports(date)]
    games: list[dict] = []
    for sp in sports:
        games += _sport_scoreboard(sp, date)
    order = {"in": 0, "pre": 1, "post": 2}
    games.sort(key=lambda g: (order.get(g.get("state"), 3), g.get("game_time") or ""))
    return games


def box_score(sport: str, game_id) -> dict:
    """Per-team player stat tables for one game (see clients for the shape)."""
    try:
        if sport == "MLB":
            return mlb_statsapi.box_score(int(game_id))
        return espn.box_score(sport, game_id)
    except Exception as e:
        log.warning("box score %s %s failed: %s", sport, game_id, e)
        return {}


def ticker_text(game: dict) -> str:
    """One compact line for the scrolling ticker."""
    a, h = game.get("away", {}), game.get("home", {})
    def s(v):
        return "" if v is None else f" {int(v) if v == int(v) else v}" if isinstance(v, (int, float)) else f" {v}"
    return (f"{a.get('abbrev') or a.get('team') or '—'}{s(a.get('score'))} @ "
            f"{h.get('abbrev') or h.get('team') or '—'}{s(h.get('score'))} · "
            f"{game.get('detail') or ''}").strip()
