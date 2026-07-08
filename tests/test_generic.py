from project547.models import generic
from project547.sports import SPORTS, active_sports, in_season


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


def test_with_consistent_margin_aligns_moneyline_and_spread():
    """After an external win-prob adjustment (Elo/rest), the rebuilt projection's
    pick'em spread cover must equal the published win prob — the two can never
    disagree. Totals must be untouched."""
    import scipy.stats as st
    wnba = SPORTS["WNBA"]
    a, b = _ratings(82, 82, 82, 82, wnba.league_ppg)
    proj = generic.project_game(wnba, a, b)
    # simulate an Elo blend pushing the home win prob up to 0.68
    adj = generic.with_consistent_margin(proj, 0.68, wnba)
    assert abs(adj.home_win_prob - 0.68) < 1e-3
    # P(margin + 0 > 0) == win prob when the margin is back-solved from it
    assert abs(adj.home_cover_prob(0, wnba) - 0.68) < 1e-3
    # implied margin mean = sigma * z(0.68)
    assert abs(adj.margin_mean - wnba.sigma_margin * st.norm.ppf(0.68)) < 0.05
    # totals are independent of the margin adjustment
    assert adj.total_mean == proj.total_mean


def test_with_consistent_margin_normal_scores_agree():
    """Normal branch: the published side scores are re-split around the
    unchanged total so they agree with the back-solved margin."""
    import pytest
    nba = SPORTS["NBA"]
    a, b = _ratings(114, 114, 114, 114, nba.league_ppg)
    proj = generic.project_game(nba, a, b)
    adj = generic.with_consistent_margin(proj, 0.63, nba)
    assert adj.home_exp - adj.away_exp == pytest.approx(adj.margin_mean, abs=1e-9)
    assert adj.home_exp + adj.away_exp == pytest.approx(adj.total_mean, abs=1e-9)
    assert adj.total_mean == proj.total_mean
    assert abs(adj.home_win_prob - 0.63) < 1e-3


def test_with_consistent_margin_tilts_poisson_lambdas():
    """Poisson sports (NHL): the lambdas are tilted so the puck line and the
    totals see the same Elo/rest blend the moneyline carries. The total is
    preserved; the analytic win prob under the tilted lambdas hits the target."""
    nhl = SPORTS["NHL"]
    good, bad = _ratings(3.8, 2.4, 2.4, 3.8, nhl.league_ppg)
    proj = generic.project_game(nhl, good, bad)
    target = round(min(0.95, proj.home_win_prob + 0.08), 4)  # Elo pushes UP
    adj = generic.with_consistent_margin(proj, target, nhl)
    assert adj.home_win_prob == round(target, 4)
    assert adj.margin_mean is None
    # total exactly preserved; the tilt is additive (+d / -d)
    assert adj.total_mean == proj.total_mean
    assert abs((adj.home_exp + adj.away_exp) - proj.total_mean) < 0.02
    # lambdas actually moved toward the (higher) target win prob
    assert adj.home_exp != proj.home_exp
    # analytic win prob at the tilted lambdas matches the target...
    assert abs(generic._analytic_poisson_win(adj.home_exp, adj.away_exp)
               - target) < 5e-3
    # ...and the simulation the cover/totals are priced from agrees (MC noise)
    assert abs(generic._poisson_win_prob(adj.home_exp, adj.away_exp)
               - target) < 0.02
    # cover prob now reflects the blend: a stronger home tilt covers -1.5 more
    raw_cover = proj.home_cover_prob(-1.5, nhl)
    assert adj.home_cover_prob(-1.5, nhl) > raw_cover


def test_with_consistent_margin_poisson_downgrade():
    """The tilt works in both directions — an Elo blend can also pull the
    favorite's win prob DOWN."""
    nhl = SPORTS["NHL"]
    good, bad = _ratings(3.8, 2.4, 2.4, 3.8, nhl.league_ppg)
    proj = generic.project_game(nhl, good, bad)
    target = max(0.05, proj.home_win_prob - 0.15)
    adj = generic.with_consistent_margin(proj, target, nhl)
    assert abs(generic._analytic_poisson_win(adj.home_exp, adj.away_exp)
               - target) < 5e-3
    assert adj.total_mean == proj.total_mean


def test_poisson_ot_resolution_one_goal():
    """Regulation ties get exactly ONE decisive goal: every tied draw finishes
    at a ±1 margin (never +2/+3), and the OT goal is included in the totals."""
    import numpy as np
    rng = np.random.default_rng(7)
    lam_h, lam_a = 3.0, 2.8
    reg_h = rng.poisson(lam_h, 20_000).astype(float)
    reg_a = rng.poisson(lam_a, 20_000).astype(float)
    reg_ties = reg_h == reg_a
    h, a = generic._poisson_draws(lam_h, lam_a)  # same seed/order as above
    margins = h - a
    assert not (margins == 0).any()                       # no unresolved ties
    assert set(np.unique(margins[reg_ties])) == {-1.0, 1.0}  # one-goal OT only
    # the OT goal is counted: tied games settle at regulation total + 1
    assert ((h + a)[reg_ties] == (reg_h + reg_a)[reg_ties] + 1).all()
    # non-tied games are untouched
    assert (h[~reg_ties] == reg_h[~reg_ties]).all()
    assert (a[~reg_ties] == reg_a[~reg_ties]).all()
    # home takes the extra goal roughly lam_h/(lam_h+lam_a) of the time
    share = float((margins[reg_ties] == 1).mean())
    assert abs(share - lam_h / (lam_h + lam_a)) < 0.05


def test_prob_push_totals():
    """Integer total lines carry push mass; half lines never push. Normal
    model uses the continuity band, Poisson counts exact simulated totals."""
    nba = SPORTS["NBA"]
    a, b = _ratings(114, 114, 114, 114, nba.league_ppg)
    proj = generic.project_game(nba, a, b)
    line = round(proj.total_mean)
    push = proj.prob_push(line, nba)
    from scipy import stats as st
    expect = (st.norm.cdf(line + 0.5, proj.total_mean, nba.sigma_total)
              - st.norm.cdf(line - 0.5, proj.total_mean, nba.sigma_total))
    assert abs(push - expect) < 1e-12 and push > 0
    assert proj.prob_push(line + 0.5, nba) == 0.0

    nhl = SPORTS["NHL"]
    good, bad = _ratings(3.8, 2.4, 2.4, 3.8, nhl.league_ppg)
    pnhl = generic.project_game(nhl, good, bad)
    p6 = pnhl.prob_push(6.0, nhl)
    assert 0.05 < p6 < 0.30            # real push mass at an NHL total of 6
    assert pnhl.prob_push(6.5, nhl) == 0.0
    # over/push/under partition the simulated totals exactly
    h, aa = pnhl._sim()
    p_over = pnhl.prob_over(6.0, nhl)
    p_under = float(((h + aa) < 6.0).mean())
    assert abs(p_over + p6 + p_under - 1.0) < 1e-12


def test_home_cover_push_prob():
    nba = SPORTS["NBA"]
    a, b = _ratings(120, 105, 105, 120, nba.league_ppg)
    proj = generic.project_game(nba, a, b)
    spread = -round(proj.home_exp - proj.away_exp)   # integer home handicap
    push = proj.home_cover_push_prob(spread, nba)
    assert push > 0
    assert proj.home_cover_push_prob(spread - 0.5, nba) == 0.0

    nhl = SPORTS["NHL"]
    good, bad = _ratings(3.8, 2.4, 2.4, 3.8, nhl.league_ppg)
    pnhl = generic.project_game(nhl, good, bad)
    # margin == +1 has mass (OT winners land there); -1.5 spreads can't push
    assert pnhl.home_cover_push_prob(-1.0, nhl) > 0.0
    assert pnhl.home_cover_push_prob(-1.5, nhl) == 0.0
    # with one-goal OT resolution the margin is never 0: pick'em can't push
    assert pnhl.home_cover_push_prob(0.0, nhl) == 0.0


def test_totals_include_ot_scoring():
    """P(over) for Poisson sports now comes from the OT-inclusive simulation:
    strictly more over mass than the regulation-only Poisson CDF at the same
    line, and over+push+under sums to 1 at integer lines."""
    from scipy import stats as st
    nhl = SPORTS["NHL"]
    a, b = _ratings(3.0, 3.0, 3.0, 3.0, nhl.league_ppg)
    proj = generic.project_game(nhl, a, b)
    lam = proj.home_exp + proj.away_exp
    p_reg_only = float(1 - st.poisson.cdf(6, lam))
    assert proj.prob_over(6.0, nhl) > p_reg_only
    # half line behaves like P(total >= 7) on the simulated totals
    assert proj.prob_over(6.5, nhl) == proj.prob_over(6.0, nhl)


def test_prop_distributions():
    # overdispersed count -> negative binomial, P(>= ~mean) a bit under 0.5
    p = generic.prop_prob_over(1.2, 0.5, "Goals")
    assert 0.45 < p < 0.75
    # points are right-skewed: at a line just under the mean, P(over) < 0.5
    p25 = generic.prop_prob_over(25, 24.5, "Points")
    assert 0.40 < p25 < 0.50
    # yards (Normal) vs points at a line above the mean
    p_yards = generic.prop_prob_over(60, 75.5, "Receiving Yards")
    p_points = generic.prop_prob_over(60, 75.5, "Points")
    assert p_yards > p_points
    # monotone in the line: a lower line gives a higher P(over)
    assert generic.prop_prob_over(25, 20.5, "Points") > p25
    # points use a smaller NB size (wider) than assists, so at a line well
    # above the mean points carry more tail mass
    assert (generic.prop_prob_over(6, 9.5, "Points")
            > generic.prop_prob_over(6, 9.5, "Assists"))


def test_neutral_site_drops_hfa():
    """neutral=True removes the ±hfa/2 tilt: an even matchup is a coin flip
    and the expected scores are symmetric."""
    for key in ("NFL", "NCAAF", "NBA"):
        sport = SPORTS[key]
        lg = sport.league_ppg
        a, b = _ratings(lg, lg, lg, lg, lg)
        h_n, a_n = generic.expected_score(sport, a, b, neutral=True)
        assert abs(h_n - a_n) < 1e-9                    # no home tilt
        h_h, a_h = generic.expected_score(sport, a, b)  # default: home site
        assert h_h - a_h > 1e-9                         # HFA present by default
        proj = generic.project_game(sport, a, b, neutral=True)
        assert abs(proj.home_win_prob - 0.5) < 1e-6
        assert generic.project_game(sport, a, b).home_win_prob > 0.5


def test_prop_push_prob():
    from scipy import stats as st
    # counting stat at an integer line -> NB pmf at that line
    push = generic.prop_push_prob(6.0, 6, "Assists")
    size = generic.NB_DISPERSION["assist"]
    p = size / (size + 6.0)
    assert abs(push - float(st.nbinom.pmf(6, size, p))) < 1e-12
    assert push > 0
    # half lines never push
    assert generic.prop_push_prob(6.0, 6.5, "Assists") == 0.0
    assert generic.prop_push_prob(60, 59.5, "Receiving Yards") == 0.0
    # yardage market at an integer line -> normal continuity band
    y = generic.prop_push_prob(60, 60, "Receiving Yards")
    sd = 0.25 * 60 + 10
    expect = st.norm.cdf(60.5, 60, sd) - st.norm.cdf(59.5, 60, sd)
    assert abs(y - expect) < 1e-12
    # over + push + under == 1 at integer lines (NB is exactly partitioned)
    over = generic.prop_prob_over(6.0, 6, "Assists")
    under = float(st.nbinom.cdf(5, size, p))
    assert abs(over + push + under - 1.0) < 1e-12


def test_multiplicative_amplifies_matchup_extremes():
    import dataclasses
    nba = SPORTS["NBA"]
    assert nba.score_method == "multiplicative"
    league = nba.league_ppg
    # elite offense (well above league) vs leaky defense (allows well above league)
    good, bad = _ratings(128, 118, 118, 128, league)
    mult = generic.expected_score(nba, good, bad)
    add = generic.expected_score(dataclasses.replace(nba, score_method="additive"),
                                 good, bad)
    # the home elite-O / away-leaky-D side scores higher under multiplicative
    assert mult[0] > add[0]
    # ...and above the simple midpoint of the two raw rates it blends
    assert mult[0] > (good.scored + bad.allowed) / 2


def test_multiplicative_matches_additive_for_average_teams():
    import dataclasses
    nba = SPORTS["NBA"]
    a, b = _ratings(114, 114, 114, 114, nba.league_ppg)
    mult = generic.expected_score(nba, a, b)
    add = generic.expected_score(dataclasses.replace(nba, score_method="additive"),
                                 a, b)
    # league-average everywhere: the two methods coincide
    assert abs(mult[0] - add[0]) < 1e-9 and abs(mult[1] - add[1]) < 1e-9


def test_sports_registry():
    assert set(SPORTS) == {"MLB", "WNBA", "NBA", "NFL", "NCAAF", "NHL",
                           "MLS", "ATP"}
    assert in_season("MLB", "2026-06-12")
    assert in_season("WNBA", "2026-06-12")
    assert not in_season("NFL", "2026-06-12")
    june = active_sports("2026-06-12")
    assert "MLB" in june and "WNBA" in june and "NCAAF" not in june
    assert "MLS" in june and "ATP" in june   # summer soccer + year-round tennis
    december = active_sports("2026-12-01")
    assert {"NBA", "NFL", "NCAAF", "NHL"} <= set(december)
