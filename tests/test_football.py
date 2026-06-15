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
    ("Passing Yards", "pass_yards"),
    ("Rushing Yards", "rush_yards"),
    ("Receiving Yards", "rec_yards"),
    ("Receptions", "receptions"),
    ("Passing Touchdowns", "pass_touchdowns"),
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
    assert qb["pass_yards"] == 320 and qb["pass_touchdowns"] == 3
    assert qb["interceptions"] == 1
    assert qb["pass_completions"] == 25 and qb["pass_attempts"] == 35
    assert qb["opponent"] == "BUF"

    rb = rows["Isiah Pacheco"]
    assert rb["rush_yards"] == 95 and rb["rush_touchdowns"] == 1
    assert rb["carries"] == 18 and rb["long_rush"] == 22
    assert rb["scrimmage_yards"] == 95 and rb["scrim_tds"] == 1

    te = rows["Travis Kelce"]
    assert te["receptions"] == 8 and te["rec_yards"] == 104
    assert te["rec_touchdowns"] == 1 and te["long_reception"] == 27
    assert te["targets"] == 10
    assert te["scrimmage_yards"] == 104 and te["scrim_tds"] == 1

    k = rows["Harrison Butker"]
    assert k["field_goals_made"] == 2 and k["kicking_points"] == 9


# --- research cards + projections from committed backdata --------------------

from onesource import teamstats  # noqa: E402
from onesource.models.generic import TeamRating, project_game  # noqa: E402
from onesource.sports import SPORTS  # noqa: E402


def _rating(df, team, season):
    d = df[(df["team"] == team) & (df["season"] == season)]
    if d.empty:
        return None
    return TeamRating(games=len(d), scored=float(d["pts"].mean()),
                      allowed=float(d["opp_pts"].mean()))


def test_nfl_matchup_built_from_backdata():
    m = teamstats.matchup("NFL", "Kansas City Chiefs", "Buffalo Bills",
                          "2025-12-15", seasons=(2025,))
    assert m and m["n_teams"] >= 20
    # team form from game results
    assert m["home_form"]["w"] + m["home_form"]["l"] >= 5
    assert m["home_form"]["last5"]
    # offense-vs-defense rows incl. yardage from player logs
    labels = [r["stat"] for r in m["home_off_vs_away_def"]]
    assert "Points/G" in labels and "Pass Yds/G" in labels
    pts = next(r for r in m["home_off_vs_away_def"] if r["stat"] == "Points/G")
    assert pts["off_season"] is not None and pts["off_rank"] is not None
    pyd = next(r for r in m["home_off_vs_away_def"] if r["stat"] == "Pass Yds/G")
    assert pyd["off_season"] is not None  # NFL player logs populate yardage


def test_nfl_projection_from_backdata_is_valid():
    df = teamstats.team_games("NFL", (2025,))
    home = _rating(df, "kansas city chiefs", 2025)
    away = _rating(df, "buffalo bills", 2025)
    assert home and away
    proj = project_game(SPORTS["NFL"], home, away)
    assert 0.0 < proj.home_win_prob < 1.0
    assert 20 < proj.total_mean < 75       # sane NFL total
    assert proj.home_exp > 0 and proj.away_exp > 0


def test_research_card_renders_from_backdata():
    from app import ui
    m = teamstats.matchup("NFL", "Kansas City Chiefs", "Buffalo Bills",
                          "2025-12-15", seasons=(2025,))
    df = teamstats.team_games("NFL", (2025,))
    proj = project_game(SPORTS["NFL"], _rating(df, "kansas city chiefs", 2025),
                        _rating(df, "buffalo bills", 2025))
    g = {"away_team": "Buffalo Bills", "home_team": "Kansas City Chiefs",
         "game_pk": "bd1", "game_time": "2025-12-15T18:00:00Z",
         "proj_total": proj.total_mean, "home_exp": proj.home_exp,
         "away_exp": proj.away_exp, "home_win_prob": proj.home_win_prob,
         "away_win_prob": 1 - proj.home_win_prob}
    html = ui.research_card_html("NFL", g, m, 0.02)
    assert "Kansas City" in html and "Buffalo" in html and "Points/G" in html


def test_nfl_player_hit_rates_from_backdata():
    hr = playerlogs.hit_rates("NFL", "Patrick Mahomes", "passing yards",
                              250.0, season=2025)
    assert hr and 0.0 <= hr["season"] <= 1.0 and hr["n_l5"] >= 1
    td = playerlogs.hit_rates("NFL", "Saquon Barkley", "anytime td", 0.5,
                              season=2025)
    assert td and 0.0 <= td["season"] <= 1.0  # scrim TDs computed from backfill


def test_team_ratings_opponent_adjustment():
    from onesource.models.generic import team_ratings
    # A and B post identical raw offense (30 twice), but A's opponent W is a
    # weak defense (allows 50 elsewhere) while B's opponent S is stingy (allows 3).
    results = [
        {"home_team": "A", "away_team": "W", "home_score": 30, "away_score": 0},
        {"home_team": "A", "away_team": "W", "home_score": 30, "away_score": 0},
        {"home_team": "B", "away_team": "S", "home_score": 30, "away_score": 0},
        {"home_team": "B", "away_team": "S", "home_score": 30, "away_score": 0},
        {"home_team": "W", "away_team": "X", "home_score": 0, "away_score": 50},
        {"home_team": "W", "away_team": "X", "home_score": 0, "away_score": 50},
        {"home_team": "S", "away_team": "Y", "home_score": 0, "away_score": 3},
        {"home_team": "S", "away_team": "Y", "home_score": 0, "away_score": 3},
    ]
    raw = team_ratings(results, 22.0, opponent_adjust=False)
    adj = team_ratings(results, 22.0, opponent_adjust=True)
    assert abs(raw["A"].scored - raw["B"].scored) < 1e-9   # equal raw offense
    assert adj["A"].scored < adj["B"].scored               # SoS separates them


def test_shift_win_prob_rest_adjustment():
    from onesource.models.generic import shift_win_prob
    assert shift_win_prob(0.5, 0.0, 13.5) == 0.5         # no delta -> no-op
    assert shift_win_prob(0.7, 5.0, 0.0) == 0.7          # no sigma (Poisson) -> no-op
    assert shift_win_prob(0.5, 3.0, 13.5) > 0.5          # home gains points
    assert shift_win_prob(0.5, -3.0, 13.5) < 0.5
    # symmetric around 0.5
    up = shift_win_prob(0.5, 3.0, 13.5) - 0.5
    dn = 0.5 - shift_win_prob(0.5, -3.0, 13.5)
    assert abs(up - dn) < 1e-9
    # bounded in (0,1)
    assert 0.0 < shift_win_prob(0.95, 20.0, 13.5) < 1.0


def test_ncaaf_matchup_builds_despite_sparse_backdata():
    # NCAAF backfill is results-only and partial — cards still build (points
    # rows populate; yardage stays empty until player logs are imported).
    df = teamstats.team_games("NCAAF", (2024,))
    assert not df.empty and {"pts", "opp_pts"} <= set(df.columns)
    top = df["team"].value_counts().index[:2].tolist()
    m = teamstats.matchup("NCAAF", top[0], top[1], "2025-02-01", seasons=(2024,))
    assert m and any(r["stat"] == "Points/G" for r in m["home_off_vs_away_def"])
