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

# Extra ESPN leagues we show scores for but don't project — so the scoreboard
# is a true one-stop board. Each value is the ESPN sport path. Leagues with no
# games on a date simply return nothing. (Leagues we already project, like NFL
# and NCAAF, are intentionally excluded — they come in as projection sports.)
EXTRA_LEAGUES = {
    "NCAAB (M)": "basketball/mens-college-basketball",
    "NCAAB (W)": "basketball/womens-college-basketball",
    "MLS": "soccer/usa.1",
    "EPL": "soccer/eng.1",
    "UCL": "soccer/uefa.champions",
}


def _sport_scoreboard(sport: str, date: str) -> list[dict]:
    try:
        if sport == "MLB":
            return mlb_statsapi.scoreboard(date)
        if sport in EXTRA_LEAGUES:
            return espn.scoreboard_at(EXTRA_LEAGUES[sport], date, sport)
        if SPORTS.get(sport) and SPORTS[sport].espn_path:
            return espn.scoreboard(sport, date)
    except Exception as e:
        log.warning("scoreboard %s %s failed: %s", sport, date, e)
    return []


def live_scoreboard(date: str, sports: list[str] | None = None) -> list[dict]:
    """Every game across in-season projection sports + the extra score-only
    leagues for a date, in-progress first."""
    if sports is None:
        sports = [s for s in SPORTS if s in active_sports(date)]
        sports += [s for s in EXTRA_LEAGUES if s not in sports]
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
        if sport in EXTRA_LEAGUES:
            return espn.box_score_at(EXTRA_LEAGUES[sport], game_id)
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
