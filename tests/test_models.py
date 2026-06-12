import math

from onesource.models import game, props
from onesource.names import normalize


def test_game_sim_symmetry():
    """Identical teams at a neutral-ish site: home edge only."""
    a = game.TeamInputs("A", runs_per_game=4.5, opp_starter_xfip=4.10)
    b = game.TeamInputs("B", runs_per_game=4.5, opp_starter_xfip=4.10)
    proj = game.simulate(a, b, draws=50_000)
    # Home team should win slightly more than half (home field runs).
    assert 0.50 < proj.home_win_prob < 0.56
    assert 8.0 < proj.total_mean < 10.0
    # Over probabilities must decrease as the line rises.
    lines = sorted(proj.over_probs)
    vals = [proj.over_probs[line] for line in lines]
    assert vals == sorted(vals, reverse=True)


def test_better_team_favored():
    good = game.TeamInputs("G", runs_per_game=5.6, opp_starter_xfip=4.8)
    bad = game.TeamInputs("B", runs_per_game=3.8, opp_starter_xfip=3.2)
    proj = game.simulate(good, bad, draws=50_000)
    assert proj.home_win_prob > 0.60


def test_pitcher_strikeouts_blend():
    m = props.pitcher_strikeouts(expected_innings=6.0, k_rate=0.30, opp_k_rate=0.25)
    # 6 IP * 4.25 BF * ~0.317 boosted rate ≈ 8 Ks
    assert 7.0 < m["mean"] < 9.0
    p_over = props.prob_over_count(m["lambda"], 6.5)
    assert 0.5 < p_over < 0.95


def test_batter_models_sane():
    h = props.batter_hits(expected_ab=4.2, ba=0.300, xba=0.290)
    assert 1.1 < h["mean"] < 1.4
    p = props.prob_over_hits(h["n"], h["p"], 0.5)  # P(1+ hits)
    assert 0.65 < p < 0.85

    tb = props.batter_total_bases(4.2, slg=0.500, xslg=0.520)
    assert 1.9 < tb["mean"] < 2.4

    hr = props.batter_home_run(4.6, hr_per_pa=0.05)
    assert 0.15 < hr["p_hr"] < 0.30


def test_blend_fallbacks():
    assert props.blend(None, None, 0.22) == 0.22
    assert props.blend(0.3, None, 0.22) == 0.3
    assert props.blend(None, 0.4, 0.22) == 0.4
    assert math.isclose(props.blend(0.2, 0.4, 0.22), 0.3)  # 50/50 default


def test_name_normalization():
    assert normalize("José Ramírez") == "jose ramirez"
    assert normalize("Ronald Acuna Jr.") == "ronald acuna"
    assert normalize("Michael Harris II") == "michael harris"
    assert normalize("J.D. Martinez") == "jd martinez"
