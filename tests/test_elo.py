from project547.models.elo import Elo, EloConfig


def test_home_edge_and_neutral_start():
    e = Elo()
    # equal ratings -> home favored by the home edge only
    p = e.home_win_prob("A", "B")
    assert 0.5 < p < 0.62


def test_higher_rated_team_favored():
    e = Elo()
    e.ratings["A"] = 1700
    e.ratings["B"] = 1400
    assert e.home_win_prob("A", "B") > 0.85
    assert e.home_win_prob("B", "A") < 0.5  # weaker team at home still underdog-ish


def test_neutral_site_drops_home_edge():
    e = Elo()
    # equal ratings on a neutral site -> a true coin flip
    assert abs(e.home_win_prob("A", "B", neutral=True) - 0.5) < 1e-12
    # rating gaps still count on neutral sites
    e.ratings["A"] = 1700
    e.ratings["B"] = 1400
    p_neutral = e.home_win_prob("A", "B", neutral=True)
    assert p_neutral > 0.8
    # ...but the home edge is gone: home quote > neutral quote
    assert e.home_win_prob("A", "B") > p_neutral


def test_update_neutral_expectation():
    """A neutral-site update scores the result against the no-edge
    expectation: for equal teams p_home = 0.5 exactly, so a home win moves
    ratings by k/2 (mov off)."""
    e = Elo(EloConfig(mov=False))
    e.update("A", "B", 1, 0, neutral=True)
    assert abs(e.ratings["A"] - (1500 + e.cfg.k * 0.5)) < 1e-9
    # a home update credits less (home win was more expected)
    e2 = Elo(EloConfig(mov=False))
    e2.update("A", "B", 1, 0)
    assert e2.ratings["A"] < e.ratings["A"]


def test_update_moves_ratings_correctly():
    e = Elo(EloConfig(mov=False))
    before = e.home_win_prob("A", "B")
    e.update("A", "B", 100, 90)  # home wins
    assert e.ratings["A"] > 1500 and e.ratings["B"] < 1500
    assert e.home_win_prob("A", "B") > before


def test_update_conserves_points_without_mov():
    e = Elo(EloConfig(mov=False))
    e.update("A", "B", 1, 0)
    assert abs((e.ratings["A"] - 1500) + (e.ratings["B"] - 1500)) < 1e-9


def test_season_regression_pulls_to_base():
    e = Elo(EloConfig(season_regress=0.5))
    e.ratings["A"] = 1700
    e._last_season["A"] = 2024
    e.home_win_prob("A", "B", season=2025)  # triggers regression for new season
    assert abs(e.ratings["A"] - 1600) < 1e-6  # halfway back to 1500


def test_mov_multiplier_increases_update():
    big = Elo(EloConfig(mov=True))
    small = Elo(EloConfig(mov=True))
    big.update("A", "B", 120, 80)    # 40-point win
    small.update("A", "B", 101, 100)  # 1-point win
    assert big.ratings["A"] > small.ratings["A"]
