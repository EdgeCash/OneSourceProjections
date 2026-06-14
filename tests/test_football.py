"""Football (NFL/NCAAF) readiness: prop distributions, market->stat mappings,
NFL week math, and ESPN football box-score parsing. These are validated with
synthetic inputs so the football path is known-good before the season opens
(live ESPN/BettingPros validation happens once games are on)."""

import pytest

from onesource import pipeline, playerlogs
from onesource.clients import espn
from onesource.models import generic
from onesource.models.generic import (DEFAULT_NB_DISPERSION, NB_DISPERSION,
                                       prop_prob_over)


def _size_for(market: str) -> float:
    name = market.lower()
    return next((v for k, v in NB_DISPERSION.items() if k in name),
               DEFAULT_NB_DISPERSION)


# --- prop distributions -----------------------------------------------------

@pytest.mark.parametrize("market,proj,line", [
    ("Passing Yards", 270.0, 249.5),
    ("Rushing Yards", 78.0, 64.5),
    ("Receiving Yards", 71.0, 59.5),
    ("Receptions", 5.5, 4.5),
    ("Passing Touchdowns", 1.9, 1.5),
    ("Field Goals Made", 1.8, 1.5),
    ("Longest Reception", 20.0, 16.5),
])
def test_prop_over_is_valid_and_favors_projection_above_line(market, proj, line):
    p = prop_prob_over(proj, line, market)
    assert 0.0 < p < 1.0
    assert p > 0.5  # projection sits above the line


@pytest.mark.parametrize("market", ["Passing Yards", "Receptions", "Field Goals Made"])
def test_prop_over_is_monotonic_in_line(market):
    proj = 50.0
    lo = prop_prob_over(proj, proj - 5, market)
    hi = prop_prob_over(proj, proj + 5, market)
    assert 0.0 < hi < lo < 1.0


def test_anytime_td_is_p_at_least_one():
    # 0.5 line on a TD count -> P(X >= 1); rises with the projection
    low = prop_prob_over(0.4, 0.5, "Anytime Touchdown")
    high = prop_prob_over(0.9, 0.5, "Anytime Touchdown")
    assert 0.0 < low < high < 1.0


def test_dispersion_routing_avoids_keyword_collisions():
    # football "field goals made" must not fall through to basketball "made"
    assert _size_for("Field Goals Made") == NB_DISPERSION["field goal"]
    # basketball "points" must not be captured by a football key (e.g. via "int")
    assert _size_for("Points") == NB_DISPERSION["point"]
    assert _size_for("3-Pointers Made") == NB_DISPERSION["made"]
    # scoring markets resolve to the near-Poisson TD dispersion
    assert _size_for("Anytime TD") == NB_DISPERSION["td"]
    assert _size_for("Passing Touchdowns") == NB_DISPERSION["touchdown"]


# --- market -> box-score stat ----------------------------------------------

@pytest.mark.parametrize("market,field", [
    ("Passing Yards", "passing_yards"),
    ("Rushing Yards", "rushing_yards"),
    ("Receiving Yards", "receiving_yards"),
    ("Receptions", "receptions"),
    ("Passing Touchdowns", "passing_tds"),
    ("Interceptions", "interceptions"),
    ("Field Goals Made", "field_goals_made"),
    ("Anytime TD", "scrim_tds"),
    ("Longest Reception", "long_reception"),
])
def test_market_to_stat_football(market, field):
    mapped = playerlogs.market_to_stat(market)
    assert mapped is not None and mapped[0] == field


# --- NFL week math ----------------------------------------------------------

def test_nfl_week_anchors_to_season_opener():
    # Sept 1 2025 is a Monday -> first Thursday is Sept 4 (Week 1)
    assert pipeline._nfl_week("2025-09-04") == 1
    assert pipeline._nfl_week("2025-09-11") == 2
    assert 9 <= pipeline._nfl_week("2025-11-16") <= 12
    # January belongs to the prior season and is clamped to the regular season
    assert pipeline._nfl_week("2026-01-04") == 18


# --- ESPN football box parsing ----------------------------------------------

def _fb_summary():
    return {"boxscore": {"players": [
        {"team": {"abbreviation": "KC"}, "statistics": [
            {"name": "passing",
             "keys": ["completions/passingAttempts", "passingYards",
                      "yardsPerPassAttempt", "passingTouchdowns", "interceptions"],
             "athletes": [{"athlete": {"displayName": "Patrick Mahomes"},
                           "stats": ["25/35", "320", "9.1", "3", "1"]}]},
            {"name": "rushing",
             "keys": ["rushingAttempts", "rushingYards", "yardsPerRushAttempt",
                      "rushingTouchdowns", "longRushing"],
             "athletes": [{"athlete": {"displayName": "Isiah Pacheco"},
                           "stats": ["18", "95", "5.3", "1", "22"]}]},
            {"name": "receiving",
             "keys": ["receptions", "receivingYards", "yardsPerReception",
                      "receivingTouchdowns", "longReception", "receivingTargets"],
             "athletes": [{"athlete": {"displayName": "Travis Kelce"},
                           "stats": ["8", "104", "13.0", "1", "27", "10"]}]},
            {"name": "kicking",
             "keys": ["fieldGoalsMade/fieldGoalAttempts", "fieldGoalPct",
                      "longFieldGoalMade", "extraPointsMade/extraPointAttempts",
                      "totalKickingPoints"],
             "athletes": [{"athlete": {"displayName": "Harrison Butker"},
                           "stats": ["2/2", "100.0", "48", "3/3", "9"]}]},
        ]},
        {"team": {"abbreviation": "BUF"}, "statistics": []},
    ]}}


def test_football_box_parses_all_categories():
    rows = {r["name"]: r for r in espn._football_box(_fb_summary(), 401)}

    qb = rows["Patrick Mahomes"]
    assert qb["passing_yards"] == 320 and qb["passing_tds"] == 3
    assert qb["interceptions"] == 1
    assert qb["completions"] == 25 and qb["pass_attempts"] == 35
    assert qb["opponent"] == "BUF"

    rb = rows["Isiah Pacheco"]
    assert rb["rushing_yards"] == 95 and rb["rushing_tds"] == 1
    assert rb["long_rush"] == 22
    assert rb["scrim_yards"] == 95 and rb["scrim_tds"] == 1

    te = rows["Travis Kelce"]
    assert te["receptions"] == 8 and te["receiving_yards"] == 104
    assert te["receiving_tds"] == 1 and te["long_reception"] == 27
    assert te["targets"] == 10
    assert te["scrim_yards"] == 104 and te["scrim_tds"] == 1

    k = rows["Harrison Butker"]
    assert k["field_goals_made"] == 2 and k["kicking_points"] == 9
