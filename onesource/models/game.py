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

    # Opposing starter adjustment: scale the starter-covered share of the
    # game by (starter xFIP / league xFIP). xFIP approximates runs allowed
    # per 9 better than ERA for projection purposes.
    if team.opp_starter_xfip is not None and team.opp_starter_xfip > 0:
        share = config.STARTER_INNINGS_SHARE
        factor = team.opp_starter_xfip / team.league_xfip
        # clamp so one hot/cold month doesn't swing a projection absurdly
        factor = float(np.clip(factor, 0.6, 1.5))
        base = base * (share * factor + (1 - share))

    if is_home:
        base += config.HOME_FIELD_RUNS / 2
    else:
        base -= config.HOME_FIELD_RUNS / 2
    return max(base, 1.5)


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
    h = rng.poisson(h_mu, n).astype(float)
    a = rng.poisson(a_mu, n).astype(float)

    # Resolve ties like extra innings: repeatedly add one-inning Poisson
    # runs for both sides until the tie breaks (vectorized, few passes).
    ties = h == a
    while ties.any():
        h[ties] += rng.poisson(h_mu / 9.0, int(ties.sum()))
        a[ties] += rng.poisson(a_mu / 9.0, int(ties.sum()))
        ties = h == a

    total = h + a
    margin = h - a

    over_probs = {}
    for line in total_lines or [7.5, 8.0, 8.5, 9.0, 9.5]:
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
