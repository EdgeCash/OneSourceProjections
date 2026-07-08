import math

from project547.models import game, props
from project547.names import normalize


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


def test_draw_runs_dispersion():
    """draw_runs hits the target mean and the requested var/mean ratio, and
    reduces exactly to Poisson at dispersion 1.0."""
    import numpy as np
    rng = np.random.default_rng(0)
    mu = 4.5
    nb = game.draw_runs(rng, mu, 200_000, dispersion=2.3)
    assert abs(nb.mean() - mu) < 0.1
    assert abs(nb.var() / nb.mean() - 2.3) < 0.15      # var/mean ≈ dispersion
    po = game.draw_runs(rng, mu, 200_000, dispersion=1.0)
    assert abs(po.var() / po.mean() - 1.0) < 0.05      # Poisson: var ≈ mean


def test_overdispersion_widens_total_tails():
    """A negative-binomial run model puts more mass in the total's tails than
    Poisson — the whole point of the fix (Poisson under-prices extreme totals)."""
    a = game.TeamInputs("A", runs_per_game=4.5, opp_starter_xfip=4.10)
    b = game.TeamInputs("B", runs_per_game=4.5, opp_starter_xfip=4.10)
    nb = game.simulate(a, b, total_lines=[12.5], draws=80_000)
    import project547.config as cfg
    old = cfg.RUN_DISPERSION
    cfg.RUN_DISPERSION = 1.0
    try:
        po = game.simulate(a, b, total_lines=[12.5], draws=80_000)
    finally:
        cfg.RUN_DISPERSION = old
    # more probability of a high total (12.5+) under overdispersion
    assert nb.over_probs[12.5] > po.over_probs[12.5]


def test_extra_innings_home_tilt():
    """Walk-off structure (audit #11): tied games break to the home side with
    HOME_EXTRA_WIN_P, so an even matchup's win prob rises above the old
    symmetric-race 0.512 as the knob rises."""
    import numpy as np
    import project547.config as cfg
    rng = np.random.default_rng(3)
    h, a = np.zeros(150_000), np.zeros(150_000)
    game.break_ties(rng, h, a, 4.5, 4.5)
    assert (h == a).sum() == 0                       # every tie resolved
    frac = (h > a).mean()
    assert abs(frac - cfg.HOME_EXTRA_WIN_P) < 0.005  # winner tilt = the knob

    ti = game.TeamInputs("T", runs_per_game=4.5, opp_starter_xfip=4.10)
    old = cfg.HOME_EXTRA_WIN_P
    try:
        cfg.HOME_EXTRA_WIN_P = 0.5
        sym = game.simulate(ti, ti, draws=200_000).home_win_prob
        cfg.HOME_EXTRA_WIN_P = 1.0     # exaggerated so the gap beats MC noise
        tilt = game.simulate(ti, ti, draws=200_000).home_win_prob
    finally:
        cfg.HOME_EXTRA_WIN_P = old
    assert tilt > sym + 0.02           # ~10% ties, all flipped home


def test_ghost_runner_adds_extra_frame_runs():
    """Audit P4 #9: tied games draw extra-frame runs at
    max(mu/9, EXTRA_FRAME_RUNS) per side — ~1 run per full extra frame at the
    0.5 floor — so weak-offense tied games no longer trickle at mu/9."""
    import numpy as np
    import project547.config as cfg
    rng = np.random.default_rng(5)
    h, a = np.zeros(100_000), np.zeros(100_000)
    game.break_ties(rng, h, a, 3.0, 3.0)   # mu/9 = 0.33 -> floor (0.5) binds
    added = (h + a).mean()
    # lambda 0.5/side -> P(frame stays tied) ≈ .466 -> E[frames] ≈ 1.87
    # -> E[added runs] = 2 * 0.5 * 1.87 ≈ 1.87 (~1 run per frame)
    assert 1.7 < added < 2.1
    per_frame_old = 2 * 3.0 / 9.0          # symmetric mu/9 rate = 0.67/frame
    assert added / 1.87 > per_frame_old    # clearly above the old rate

    # knob at ~0 restores the mu/9-only behavior (fewer runs added)
    old = cfg.EXTRA_FRAME_RUNS
    try:
        cfg.EXTRA_FRAME_RUNS = 1e-9
        h2, a2 = np.zeros(100_000), np.zeros(100_000)
        game.break_ties(rng, h2, a2, 3.0, 3.0)
    finally:
        cfg.EXTRA_FRAME_RUNS = old
    assert (h2 + a2).mean() < added


def test_home_win_shift_applies_post_sim():
    import project547.config as cfg
    ti = game.TeamInputs("T", runs_per_game=4.5, opp_starter_xfip=4.10)
    old = cfg.HOME_WIN_SHIFT
    try:
        cfg.HOME_WIN_SHIFT = 0.0
        base = game.simulate(ti, ti, draws=20_000).home_win_prob
        cfg.HOME_WIN_SHIFT = 0.02
        shifted = game.simulate(ti, ti, draws=20_000).home_win_prob
    finally:
        cfg.HOME_WIN_SHIFT = old
    assert abs(shifted - (base + 0.02)) < 1e-9


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


def test_refine_expected_innings():
    # no workload -> unchanged
    assert props.refine_expected_innings(6.0, None) == 6.0
    assert props.refine_expected_innings(6.0, {"n_starts": 1}) == 6.0
    # a starter on a limit (low pitch ceiling) is pulled DOWN from his season avg
    limited = {"n_starts": 3, "pitch_ceiling": 60, "pitches_per_out": 5.0}
    # budget = (60/5)/3 = 4.0 innings; exp = 0.5*6 + 0.5*4 = 5.0
    assert props.refine_expected_innings(6.0, limited) == 5.0
    # an established starter (ceiling supports his line) stays ~unchanged...
    established = {"n_starts": 4, "pitch_ceiling": 102, "pitches_per_out": 5.6}
    exp = props.refine_expected_innings(6.0, established)
    assert 5.9 < exp <= 6.5
    # ...and the upside is capped at +0.5 even for a monster ceiling
    monster = {"n_starts": 4, "pitch_ceiling": 130, "pitches_per_out": 5.0}
    assert props.refine_expected_innings(6.0, monster) == 6.5
    # floor clamp holds: a low base + tiny ceiling can't drop below 3.0
    # budget=(20/6)/3=1.11, exp=0.5*3.2+0.5*1.11=2.15 -> clamped up to 3.0
    assert props.refine_expected_innings(3.2,
        {"n_starts": 3, "pitch_ceiling": 20, "pitches_per_out": 6.0}) == 3.0


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
    from project547.models import props as pm
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
    from project547 import pipeline
    base = {"dist": "negbinom", "param": 16.0}
    # a high-dispersion (tight) outs line vs the default total-bases dispersion
    tight = pipeline.prob_over_for_row({**base, "dispersion": 40.0}, 16.5)
    loose = pipeline.prob_over_for_row({**base, "dispersion": 1.1}, 16.5)
    assert tight != loose
    # missing dispersion (batter rows) falls back without error
    assert pipeline.prob_over_for_row(base, 16.5) is not None


def test_pitcher_prop_stats_exposed_for_grading():
    import pandas as pd
    from project547 import playerlogs as pl
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
