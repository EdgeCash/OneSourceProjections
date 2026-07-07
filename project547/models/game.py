"""Game-level model: project expected runs for each side, then Monte Carlo
a Poisson run distribution to get moneyline / total / run line probabilities.

Expected runs = shrunk recent team scoring rate, adjusted for the opposing
starter's quality (xFIP vs league) over the innings the starter covers,
plus home-field advantage. Deliberately simple and transparent — every
number in the chain is inspectable on the dashboard.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .. import config


@dataclass
class TeamInputs:
    name: str
    runs_per_game: float          # recent scoring rate (raw)
    opp_starter_xfip: float | None  # opposing starter's xFIP (None = unknown)
    league_xfip: float = 4.10
    opp_bullpen_xfip: float | None = None  # opposing bullpen FIP (None = unknown)
    park_factor: float = 1.0      # run factor of the game's venue (1.0 neutral)
    own_home_pf: float = 1.0      # team's own home-park factor (de-bias the rate)
    temp_f: float | None = None   # first-pitch temperature (None = unknown/dome)
    ump_runs_factor: float = 1.0  # home-plate ump run multiplier (1.0 = neutral)
    lineup_woba: float | None = None  # mean wOBA of the 9 posted batters (None =
    # no lineup posted -> fall back to the team-rate base, current behavior)
    league_woba: float = 0.318    # league wOBA anchor for the lineup engine's
    # wRAA. The pipeline sets this from batwoba.league_woba(season) (PA-weighted
    # mean of the season's Statcast file) so the anchor tracks the real run
    # environment; 0.318 stays the default because the AVG/SLG proxy wOBA is
    # anchored to it by construction (pipeline._batter_woba_map).
    wind_out: float | None = None  # signed out/in wind component: +1 = straight
    # out to CF (boosts runs), -1 = straight in, 0 = crosswind (None = unknown/dome)
    wind_mph: float | None = None  # wind speed at first pitch


@dataclass
class GameProjection:
    home_exp_runs: float
    away_exp_runs: float
    home_win_prob: float
    total_mean: float
    over_probs: dict[float, float]    # line -> P(over)
    home_runline_cover: dict[float, float]  # spread -> P(home covers)


# Lineup-level offense (roadmap T2.2). Best-in-class systems (THE BAT X,
# ZiPS/FanGraphs game odds) build team runs from the nine posted batters vs the
# starter, not a team-average rate. This is that construction in its simplest,
# provable linear-weights form: a lineup's mean wOBA converts to a
# park/pitcher-neutral team run estimate via wRAA = PA·(wOBA − lgwOBA)/scale.
LEAGUE_WOBA = 0.318            # matches models/nrfi.LEAGUE_WOBA
WOBA_RUN_SCALE = 1.20         # wOBA points per run above average (standard ~1.15–1.25)
TEAM_PA_PER_GAME = 38.0       # plate appearances a lineup gets over 9 innings


def lineup_offense_runs(lineup_woba: float, league_runs: float | None = None,
                        league_woba: float | None = None) -> float:
    """Convert a lineup's mean wOBA into a park/pitcher-neutral team run estimate.

    wRAA/game = PA·(wOBA − lgwOBA)/scale, added to the league run baseline — the
    ZiPS/THE-BAT "runs from the nine posted batters" idea, linearized. The
    downstream opposing-pitching / park / temperature modifiers in expected_runs
    still apply on top, so this only replaces the *offensive* base.

    ``league_woba`` is the wRAA anchor. When batters carry real Statcast wOBA the
    caller should pass the season's PA-weighted league mean (batwoba.league_woba)
    — the committed files range .310–.318 by season, and anchoring a .310 season
    to the 0.318 constant read every lineup ~"below league" (audit #12). None
    keeps the 0.318 default that the AVG/SLG proxy path is built around."""
    league = config.LEAGUE_RUNS_PER_GAME if league_runs is None else league_runs
    anchor = LEAGUE_WOBA if league_woba is None else league_woba
    wraa = TEAM_PA_PER_GAME * (lineup_woba - anchor) / WOBA_RUN_SCALE
    return max(league + wraa, 1.0)


def expected_runs(team: TeamInputs, is_home: bool) -> float:
    league = config.LEAGUE_RUNS_PER_GAME
    w = config.TEAM_RATE_WEIGHT
    base = w * team.runs_per_game + (1 - w) * league

    # Lineup-level offense: blend the run estimate from the nine posted batters
    # into the team-rate base (inert at LINEUP_BLEND=0 / no lineup posted).
    if team.lineup_woba is not None and config.LINEUP_BLEND > 0:
        lr = lineup_offense_runs(team.lineup_woba, league, team.league_woba)
        base = config.LINEUP_BLEND * lr + (1 - config.LINEUP_BLEND) * base

    # Opposing pitching: scale the starter-covered share of the game by the
    # starter's quality and the rest by the opposing bullpen's quality, each
    # as (FIP / league_FIP). FIP/xFIP approximate runs allowed per 9 better
    # than ERA for projection. Clamp so one hot/cold month can't swing it.
    share = config.STARTER_INNINGS_SHARE
    sp_factor = bp_factor = 1.0
    if team.opp_starter_xfip is not None and team.opp_starter_xfip > 0:
        sp_factor = float(np.clip(team.opp_starter_xfip / team.league_xfip, 0.6, 1.5))
    if team.opp_bullpen_xfip is not None and team.opp_bullpen_xfip > 0:
        bp_factor = float(np.clip(team.opp_bullpen_xfip / team.league_xfip, 0.6, 1.5))
    if team.opp_starter_xfip or team.opp_bullpen_xfip:
        base = base * (share * sp_factor + (1 - share) * bp_factor)

    # Park: the recent rate is ~half-baked at the team's own park, so
    # de-bias by its home factor, then apply the venue's factor. Tunable
    # weight tempers the adjustment.
    if team.park_factor != 1.0 or team.own_home_pf != 1.0:
        own_bias = 0.5 * team.own_home_pf + 0.5
        park_mult = team.park_factor / own_bias
        base = base * (1 + config.PARK_WEIGHT * (park_mult - 1))

    # Temperature: warm air carries the ball -> more scoring. A game-level
    # multiplier (same for both teams) so it moves the total, not the margin.
    # Skipped when temp is unknown (domes/closed roofs pass temp_f=None).
    if team.temp_f is not None and config.TEMP_COEF:
        adj = config.TEMP_COEF * (float(team.temp_f) - config.TEMP_BASELINE_F)
        adj = float(np.clip(adj, -config.TEMP_CLAMP, config.TEMP_CLAMP))
        base = base * (1 + adj)

    # Wind: blowing out to center carries balls for extra runs, blowing in
    # suppresses them; a game-level multiplier scaled by the out/in component and
    # speed. Skipped for domes / unknown direction (wind_out=None). Gated by
    # WIND_COEF (0 = off) and clamped, exactly like the temperature term.
    if (team.wind_out is not None and team.wind_mph is not None
            and config.WIND_COEF):
        wadj = config.WIND_COEF * team.wind_out * float(team.wind_mph)
        wadj = float(np.clip(wadj, -config.WIND_CLAMP, config.WIND_CLAMP))
        base = base * (1 + wadj)

    # Home-plate umpire: a game-level multiplier (same for both teams, so it
    # moves the total not the margin), already shrunk + clamped in umpires.py.
    # 1.0 (neutral) when the crew isn't posted or the weight is off.
    if team.ump_runs_factor and team.ump_runs_factor != 1.0:
        base = base * team.ump_runs_factor

    if is_home:
        base += config.HOME_FIELD_RUNS / 2
    else:
        base -= config.HOME_FIELD_RUNS / 2
    return max(base, 1.5)


# Every realistic MLB game total, at half-run granularity. simulate() prices
# P(over) at each from the negative-binomial draws, so the pipeline can read the
# probability for whatever line the book posts straight off the simulation —
# never falling back to a different (Poisson) distribution than the one we drew.
TOTAL_LINE_GRID = [x / 2 for x in range(12, 31)]  # 6.0, 6.5, ... 15.0


def draw_runs(rng, mu: float, n: int, dispersion: float | None = None) -> np.ndarray:
    """Draw ``n`` game run totals with mean ``mu``. Real MLB runs are
    overdispersed (var/mean ≈ 2.3), so when ``dispersion`` > 1 we sample from a
    negative binomial via its gamma-Poisson mixture: Poisson(λ) with
    λ ~ Gamma(shape=size, scale=mu/size), size = mu/(d-1), giving mean ``mu``
    and variance ``mu·d``. ``dispersion`` ≤ 1 (or non-positive mean) falls back
    to the exact Poisson behavior."""
    d = config.RUN_DISPERSION if dispersion is None else dispersion
    if d <= 1.0 or mu <= 0:
        return rng.poisson(mu, n).astype(float)
    size = mu / (d - 1.0)
    lam = rng.gamma(shape=size, scale=mu / size, size=n)
    return rng.poisson(lam).astype(float)


def break_ties(rng, h: np.ndarray, a: np.ndarray, h_mu: float, a_mu: float) -> None:
    """Resolve 9-inning ties like extra innings, in place (audit #11 / P4 #9).

    Runs added to the TOTAL: each extra frame both sides draw one-inning
    Poisson runs until the tie breaks (vectorized, few passes). The per-side
    frame lambda is max(mu/9, EXTRA_FRAME_RUNS): since 2020 the ghost runner
    (automatic runner on 2nd) makes extra frames score well above a normal
    inning of the same teams, so a floor keeps tied-game totals from being
    understated. Single innings, so Poisson is appropriate here regardless of
    the full-game dispersion.

    The WINNER: real extra-inning games are not a symmetric run race — the
    home side bats last and plays every bottom half knowing exactly what it
    needs (walk-off structure), which is why home teams win extras ≈52%
    historically. A symmetric race can't produce that, so the winning side of
    each tied game is drawn Bernoulli(HOME_EXTRA_WIN_P) and the frame runs are
    assigned larger-to-winner — the tie-break's total and margin-magnitude
    distributions are untouched; only which side ends up ahead is tilted."""
    ties = h == a
    n_t = int(ties.sum())
    if not n_t:
        return
    lam_h = max(h_mu / 9.0, config.EXTRA_FRAME_RUNS)
    lam_a = max(a_mu / 9.0, config.EXTRA_FRAME_RUNS)
    hx = np.zeros(n_t)
    ax = np.zeros(n_t)
    open_ = np.ones(n_t, dtype=bool)
    while open_.any():
        k = int(open_.sum())
        hx[open_] += rng.poisson(lam_h, k)
        ax[open_] += rng.poisson(lam_a, k)
        open_ = hx == ax
    home_wins = rng.random(n_t) < config.HOME_EXTRA_WIN_P
    hi, lo = np.maximum(hx, ax), np.minimum(hx, ax)
    h[ties] += np.where(home_wins, hi, lo)
    a[ties] += np.where(home_wins, lo, hi)


def simulate(
    home: TeamInputs,
    away: TeamInputs,
    total_lines: list[float] | None = None,
    runline_spreads: list[float] | None = None,
    draws: int | None = None,
    seed: int | None = 7,
) -> GameProjection:
    h_mu = expected_runs(home, is_home=True)
    a_mu = expected_runs(away, is_home=False)

    rng = np.random.default_rng(seed)
    n = draws or config.SIM_DRAWS
    h = draw_runs(rng, h_mu, n)
    a = draw_runs(rng, a_mu, n)

    break_ties(rng, h, a, h_mu, a_mu)

    total = h + a
    margin = h - a

    over_probs = {}
    for line in total_lines or TOTAL_LINE_GRID:
        over_probs[line] = float((total > line).mean())

    cover = {}
    for spread in runline_spreads or [-1.5, 1.5]:
        cover[spread] = float((margin + spread > 0).mean())

    # HOME_WIN_SHIFT: small additive post-sim home win-prob adjustment for the
    # residual last-at-bat edge the run model can't express (audit #11); 0 = off.
    hwp = float(np.clip((margin > 0).mean() + config.HOME_WIN_SHIFT, 0.0, 1.0))

    return GameProjection(
        home_exp_runs=round(h_mu, 3),
        away_exp_runs=round(a_mu, 3),
        home_win_prob=hwp,
        total_mean=round(h_mu + a_mu, 3),
        over_probs=over_probs,
        home_runline_cover=cover,
    )
