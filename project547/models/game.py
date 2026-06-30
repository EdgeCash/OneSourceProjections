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


@dataclass
class GameProjection:
    home_exp_runs: float
    away_exp_runs: float
    home_win_prob: float
    total_mean: float
    over_probs: dict[float, float]    # line -> P(over)
    home_runline_cover: dict[float, float]  # spread -> P(home covers)


def expected_runs(team: TeamInputs, is_home: bool) -> float:
    league = config.LEAGUE_RUNS_PER_GAME
    w = config.TEAM_RATE_WEIGHT
    base = w * team.runs_per_game + (1 - w) * league

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

    # Resolve ties like extra innings: repeatedly add one-inning runs for both
    # sides until the tie breaks (vectorized, few passes). Extra frames are
    # single innings, so Poisson is appropriate here regardless of the
    # full-game dispersion.
    ties = h == a
    while ties.any():
        h[ties] += rng.poisson(h_mu / 9.0, int(ties.sum()))
        a[ties] += rng.poisson(a_mu / 9.0, int(ties.sum()))
        ties = h == a

    total = h + a
    margin = h - a

    over_probs = {}
    for line in total_lines or TOTAL_LINE_GRID:
        over_probs[line] = float((total > line).mean())

    cover = {}
    for spread in runline_spreads or [-1.5, 1.5]:
        cover[spread] = float((margin + spread > 0).mean())

    return GameProjection(
        home_exp_runs=round(h_mu, 3),
        away_exp_runs=round(a_mu, 3),
        home_win_prob=float((margin > 0).mean()),
        total_mean=round(h_mu + a_mu, 3),
        over_probs=over_probs,
        home_runline_cover=cover,
    )
