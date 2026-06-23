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


def test_hits_overdispersion_fix():
    """The beta-binomial fix works through zero-inflation on the common 0.5
    line (most batters average <1 hit): adding zero-hit games lowers P(>=1),
    which removed the +2.6pt over-bias. Guard that mechanism + monotonicity."""
    from scipy import stats
    n, p = 5, 0.20                     # mean 1.0; line 0.5 -> P(>=1 hit)
    bb = props.prob_over_hits(n, p, 0.5)
    binom = float(1 - stats.binom.cdf(0, n, p))
    assert 0.0 < bb < binom < 1.0      # overdispersion -> more zeros -> lower P(over)
    # less overdispersion (higher concentration) -> back toward the binomial
    assert bb < props.prob_over_hits(n, p, 0.5, concentration=200) <= binom
    assert (props.prob_over_hits(n, p, 0.5)
            > props.prob_over_hits(n, p, 1.5)
            > props.prob_over_hits(n, p, 2.5))            # monotonic in line

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


def test_pitcher_markets_means_and_blend():
    from onesource.models import props as pm
    # outs = innings*3; FP projection blends in
    assert pm.pitcher_outs(5.0)["mean"] == 15.0
    blended = pm.pitcher_outs(5.0, fp_projected_outs=18.0)["mean"]
    assert 15.0 < blended < 18.0  # 50/50 blend lands between
    # own per-inning rate beats league when provided
    hi = pm.pitcher_hits_allowed(6.0, h_per_inning=1.2)["mean"]
    lo = pm.pitcher_hits_allowed(6.0, h_per_inning=0.6)["mean"]
    assert hi > lo
    # dispersions: earned runs spikiest (smallest size), outs least dispersed
    assert (pm.pitcher_earned_runs(5.0)["dispersion"]
            < pm.pitcher_walks(5.0)["dispersion"]
            < pm.pitcher_outs(5.0)["dispersion"])


def test_prob_over_for_row_uses_row_dispersion():
    from onesource import pipeline
    base = {"dist": "negbinom", "param": 16.0}
    # a high-dispersion (tight) outs line vs the default total-bases dispersion
    tight = pipeline.prob_over_for_row({**base, "dispersion": 40.0}, 16.5)
    loose = pipeline.prob_over_for_row({**base, "dispersion": 1.1}, 16.5)
    assert tight != loose
    # missing dispersion (batter rows) falls back without error
    assert pipeline.prob_over_for_row(base, 16.5) is not None


def test_pitcher_prop_stats_exposed_for_grading():
    import pandas as pd
    from onesource import playerlogs as pl
    df = pd.DataFrame([{
        "name": "Zack Wheeler", "date": "2026-06-21", "opponent": "NYM",
        "season": 2026, "inningsPitched": 5.667, "hits": 4, "baseOnBalls": 2,
        "earnedRuns": 3, "strikeOuts": 8}])
    base = pl._normalize_frame("mlb", df)
    assert base["outs"].iloc[0] == 17.0          # 5⅔ innings -> 17 outs
    assert base["hits"].iloc[0] == 4             # pitcher hits = hits allowed
    assert base["earnedRuns"].iloc[0] == 3
    assert base["baseOnBalls"].iloc[0] == 2
    # market map points at the columns we now expose
    assert pl.market_to_stat("pitcher_outs") == ("outs", "P")
    assert pl.market_to_stat("pitcher_hits_allowed") == ("hits", "P")
    assert pl.market_to_stat("pitcher_earned_runs") == ("earnedRuns", "P")
    assert pl.market_to_stat("pitcher_walks") == ("baseOnBalls", "P")
