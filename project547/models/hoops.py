"""Pace-and-efficiency scoring model for basketball (roadmap T2.1).

Best-in-class basketball projections (KenPom, RAPTOR, Inpredictable) never start
from a raw points-per-game average — they project **possessions × points per 100
possessions**, separating *how fast* a game is played from *how efficiently* each
side scores. A raw-PPG midpoint compresses exactly the matchups that move a total:
two fast, efficient teams; a slow team throttling a fast one. This module is that
construction, kept deliberately simple and provable.

Ratings (pace, offensive rating = pts/100 poss, defensive rating = opp pts/100)
are built walk-forward from team box scores (teamstats.team_games), shrunk toward
league. The projection combines them with a log5-style efficiency matchup so a
strong offense vs a weak defense scores above either team's mean.
"""
from __future__ import annotations

from dataclasses import dataclass

RATING_SHRINK = 0.75      # weight on observed rate vs league (pace/rtg are stabler than PPG)


@dataclass
class HoopsRating:
    games: int
    pace: float           # possessions per game
    off_rtg: float        # points scored per 100 possessions
    def_rtg: float        # points allowed per 100 possessions


def project_points(home: HoopsRating, away: HoopsRating,
                   league_pace: float, league_rtg: float,
                   hfa: float = 0.0) -> tuple[float, float]:
    """Return (home_points, away_points).

    Expected possessions is the average of the two teams' pace (both sides play
    the same number of possessions in a game). Each side's efficiency is a
    log5 blend of its offense and the opponent's defense against league:
    ``eff = off_rtg · opp_def_rtg / league_rtg`` — points per 100 poss. Points =
    possessions · eff / 100, with home-court split symmetrically across the total.
    """
    poss = 0.5 * (home.pace + away.pace)
    h_eff = home.off_rtg * away.def_rtg / league_rtg
    a_eff = away.off_rtg * home.def_rtg / league_rtg
    h_pts = poss * h_eff / 100.0 + hfa / 2.0
    a_pts = poss * a_eff / 100.0 - hfa / 2.0
    return max(h_pts, league_rtg * 0.3), max(a_pts, league_rtg * 0.3)


def shrink(value: float | None, n: int, league: float,
           full_at: int = 10) -> float:
    """Shrink an observed per-team rate toward league by sample size."""
    if value is None or n <= 0:
        return league
    w = RATING_SHRINK * min(1.0, n / full_at)
    return w * value + (1 - w) * league
