from onesource import parks
from onesource.models import game


def test_factor_resolves_names_and_abbrevs():
    # full name, nickname, city, and abbrev variants all resolve
    col = parks.factor("Colorado Rockies")
    assert col > 1.10  # Coors inflates
    assert parks.factor("COL") == col
    assert parks.factor("rockies") == col
    sea = parks.factor("Seattle Mariners")
    assert sea < 0.95  # pitcher park
    # abbrev variants map to the same park
    assert parks.factor("OAK") == parks.factor("ATH")
    assert parks.factor("AZ") == parks.factor("Arizona Diamondbacks")


def test_unknown_team_is_neutral():
    assert parks.factor("Nonexistent Team") == 1.0
    assert parks.factor("") == 1.0


def test_league_mean_near_one():
    f = parks._factors()
    assert abs(sum(f.values()) / len(f) - 1.0) < 0.01


def test_model_applies_park_factor():
    # same matchup, hitter park vs pitcher park -> more/fewer expected runs
    base = game.TeamInputs("x", runs_per_game=4.5, opp_starter_xfip=None)
    hi = game.TeamInputs("x", runs_per_game=4.5, opp_starter_xfip=None,
                         park_factor=1.19, own_home_pf=1.0)
    lo = game.TeamInputs("x", runs_per_game=4.5, opp_starter_xfip=None,
                         park_factor=0.90, own_home_pf=1.0)
    rb = game.expected_runs(base, is_home=False)
    rh = game.expected_runs(hi, is_home=False)
    rl = game.expected_runs(lo, is_home=False)
    assert rh > rb > rl


def test_model_applies_bullpen_factor():
    good_bp = game.TeamInputs("x", runs_per_game=4.5, opp_starter_xfip=4.1,
                              opp_bullpen_xfip=3.0)   # tough bullpen
    bad_bp = game.TeamInputs("x", runs_per_game=4.5, opp_starter_xfip=4.1,
                             opp_bullpen_xfip=5.5)    # weak bullpen
    assert game.expected_runs(bad_bp, is_home=False) > game.expected_runs(good_bp, is_home=False)
