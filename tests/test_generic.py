from onesource.models import generic
from onesource.sports import SPORTS, active_sports, in_season


def _ratings(home_ppg, home_allowed, away_ppg, away_allowed, league):
    results = []
    for _ in range(15):
        results.append({"home_team": "A", "away_team": "X",
                        "home_score": home_ppg, "away_score": home_allowed})
        results.append({"home_team": "B", "away_team": "Y",
                        "home_score": away_ppg, "away_score": away_allowed})
    r = generic.team_ratings(results, league)
    return r["A"], r["B"]


def test_normal_model_better_team_favored():
    nba = SPORTS["NBA"]
    good, bad = _ratings(120, 105, 105, 120, nba.league_ppg)
    proj = generic.project_game(nba, good, bad)
    assert proj.home_win_prob > 0.70
    assert proj.home_exp > proj.away_exp


def test_normal_model_even_matchup_home_edge():
    nfl = SPORTS["NFL"]
    a, b = _ratings(22.5, 22.5, 22.5, 22.5, nfl.league_ppg)
    proj = generic.project_game(nfl, a, b)
    assert 0.50 < proj.home_win_prob < 0.58


def test_unknown_teams_fall_back_to_league_average():
    wnba = SPORTS["WNBA"]
    proj = generic.project_game(wnba, None, None)
    assert abs(proj.total_mean - 2 * wnba.league_ppg) < 3.5


def test_poisson_model_nhl():
    nhl = SPORTS["NHL"]
    good, bad = _ratings(3.8, 2.4, 2.4, 3.8, nhl.league_ppg)
    proj = generic.project_game(nhl, good, bad)
    assert proj.home_win_prob > 0.60
    p_over = proj.prob_over(5.5, nhl)
    p_over_high = proj.prob_over(7.5, nhl)
    assert p_over > p_over_high


def test_total_and_cover_probabilities():
    nba = SPORTS["NBA"]
    a, b = _ratings(114, 114, 114, 114, nba.league_ppg)
    proj = generic.project_game(nba, a, b)
    assert abs(proj.prob_over(proj.total_mean, nba) - 0.5) < 0.01
    # home favorite by hfa: pick-em spread should cover slightly over half
    assert proj.home_cover_prob(0, nba) > 0.5
    assert proj.home_cover_prob(-10, nba) < proj.home_cover_prob(10, nba)


def test_prop_distributions():
    # small count -> poisson
    p = generic.prop_prob_over(1.2, 0.5, "Goals")
    assert 0.6 < p < 0.8
    # basketball points -> normal
    p25 = generic.prop_prob_over(25, 24.5, "Points")
    assert 0.5 < p25 < 0.6
    # yards get a wider sd than points at the same mean
    p_yards = generic.prop_prob_over(60, 75.5, "Receiving Yards")
    p_points = generic.prop_prob_over(60, 75.5, "Points")
    assert p_yards > p_points
    # monotone in the line
    assert generic.prop_prob_over(25, 20.5, "Points") > p25


def test_sports_registry():
    assert set(SPORTS) == {"MLB", "WNBA", "NBA", "NFL", "NCAAF", "NHL"}
    assert in_season("MLB", "2026-06-12")
    assert in_season("WNBA", "2026-06-12")
    assert not in_season("NFL", "2026-06-12")
    june = active_sports("2026-06-12")
    assert "MLB" in june and "WNBA" in june and "NCAAF" not in june
    december = active_sports("2026-12-01")
    assert {"NBA", "NFL", "NCAAF", "NHL"} <= set(december)
