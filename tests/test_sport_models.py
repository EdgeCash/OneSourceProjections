"""Readiness checks for every sport's game model — so the out-of-season sports
(NBA, NHL, NFL, NCAAF) are known-good the moment their season flips on."""

import pytest

from onesource.models import generic
from onesource.models.elo import Elo, EloConfig
from onesource.models.generic import TeamRating
from onesource.sports import SPORTS

GENERIC = ["WNBA", "NBA", "NFL", "NCAAF", "NHL"]


def _ratings(sport):
    lg = sport.league_ppg
    good = TeamRating(games=10, scored=lg * 1.10, allowed=lg * 0.90)
    bad = TeamRating(games=10, scored=lg * 0.90, allowed=lg * 1.10)
    even = TeamRating(games=10, scored=lg, allowed=lg)
    return good, bad, even


@pytest.mark.parametrize("key", GENERIC)
def test_better_home_team_is_favored_and_outputs_valid(key):
    sport = SPORTS[key]
    good, bad, _ = _ratings(sport)
    proj = generic.project_game(sport, good, bad)
    assert 0.5 < proj.home_win_prob < 1.0           # strong home team favored
    assert proj.total_mean > 0
    assert proj.home_exp > proj.away_exp
    # totals are valid probabilities and monotonically decreasing in the line
    lo = proj.prob_over(proj.total_mean - 5, sport)
    hi = proj.prob_over(proj.total_mean + 5, sport)
    assert 0.0 < hi < lo < 1.0
    # cover prob valid; at spread 0 it should track the win prob direction
    assert 0.0 < proj.home_cover_prob(0.0, sport) < 1.0


@pytest.mark.parametrize("key", GENERIC)
def test_home_field_advantage_tilts_even_matchup_home(key):
    sport = SPORTS[key]
    _, _, even = _ratings(sport)
    proj = generic.project_game(sport, even, even)
    assert proj.home_win_prob > 0.5                  # HFA gives the home edge


@pytest.mark.parametrize("key", GENERIC)
def test_sport_config_is_sane(key):
    s = SPORTS[key]
    assert s.league_ppg > 0
    assert 0.0 <= s.elo_blend <= 1.0
    assert s.elo_k > 0 and s.elo_home_edge >= 0 and 0.0 <= s.elo_regress <= 1.0
    if s.model == "normal":
        assert s.sigma_margin > 0 and s.sigma_total > 0
    else:
        assert s.model == "poisson"


def test_dormant_sports_are_primed_with_elo():
    # the out-of-season team sports should now carry prior-season Elo so their
    # openers aren't coin flips (MLB runs its own pipeline, no Elo blend)
    for key in ("NBA", "NHL", "NFL", "NCAAF", "WNBA"):
        assert SPORTS[key].elo_blend > 0, key
    assert SPORTS["MLB"].elo_blend == 0


@pytest.mark.parametrize("key", ["WNBA", "NBA", "NFL", "NCAAF", "NHL"])
def test_per_sport_elo_responds_to_results(key):
    s = SPORTS[key]
    cfg = EloConfig(k=s.elo_k, home_edge=s.elo_home_edge,
                    season_regress=s.elo_regress)
    e = Elo(cfg)
    assert e.home_win_prob("H", "A") > 0.5           # home edge baked in
    base_diff = e.ratings.get("H", cfg.base) - e.ratings.get("A", cfg.base)
    # feed several home wins; the home side's rating must climb above the away
    for _ in range(8):
        e.update("H", "A", s.league_ppg * 1.3, s.league_ppg * 0.7)
    assert e.ratings["H"] > e.ratings["A"] > 0
    assert (e.ratings["H"] - e.ratings["A"]) > base_diff


def test_elo_regresses_between_seasons():
    cfg = EloConfig(k=20, home_edge=65, season_regress=0.5)
    e = Elo(cfg)
    e.update("H", "A", 120, 90, season=2024)         # H climbs above base
    high = e.ratings["H"]
    assert high > cfg.base
    # a new season pulls the rating halfway back toward base
    e.home_win_prob("H", "A", season=2025)
    assert cfg.base < e.ratings["H"] < high
