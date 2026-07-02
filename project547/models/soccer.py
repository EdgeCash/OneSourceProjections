"""Soccer match model — 1X2 (home / draw / away), totals, and both-teams-to-score
from each side's expected goals.

Goals are modelled as (nearly) independent Poisson, with the classic Dixon-Coles
low-score correction: independent Poisson slightly under-counts 1-1 and 0-0 draws
and over-counts 1-0 / 0-1, so a single dependence parameter ``rho`` re-weights the
four low-score cells. Expected goals for each side come from the shared team
off/def ratings (``models/generic.team_ratings`` in goals) plus home-field, so
this module is just the pure scoreline distribution on top.

v1, deliberately simple and provable: match probabilities from expected goals,
no bookmaker margin, surfaced behind the demonstrated-edge gate like every market
(a new market opens in PROBATION and only earns stake as it proves CLV).
"""
from __future__ import annotations

import math

MAX_GOALS = 12          # truncate the scoreline grid; P(>12 in a half) ≈ 0
RHO = -0.13             # Dixon-Coles low-score dependence (draw-inflating); a
                        # standard fitted value for league football, kept fixed.


def _pois_pmf(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * lam ** k / math.factorial(k)


def _dc_tau(i: int, j: int, lam: float, mu: float, rho: float) -> float:
    """Dixon-Coles adjustment factor for the four lowest scorelines."""
    if i == 0 and j == 0:
        return 1.0 - lam * mu * rho
    if i == 0 and j == 1:
        return 1.0 + lam * rho
    if i == 1 and j == 0:
        return 1.0 + mu * rho
    if i == 1 and j == 1:
        return 1.0 - rho
    return 1.0


def score_matrix(exp_home: float, exp_away: float, rho: float = RHO) -> list[list[float]]:
    """Normalised P(home i, away j) grid over 0..MAX_GOALS with the DC tweak."""
    lam = max(exp_home, 1e-6)
    mu = max(exp_away, 1e-6)
    ph = [_pois_pmf(i, lam) for i in range(MAX_GOALS + 1)]
    pa = [_pois_pmf(j, mu) for j in range(MAX_GOALS + 1)]
    grid = [[ph[i] * pa[j] * _dc_tau(i, j, lam, mu, rho)
             for j in range(MAX_GOALS + 1)] for i in range(MAX_GOALS + 1)]
    s = sum(v for row in grid for v in row)
    if s > 0:
        grid = [[v / s for v in row] for row in grid]
    return grid


def outcome_probs(exp_home: float, exp_away: float, rho: float = RHO) -> dict:
    """{home, draw, away} match-result probabilities (sum to 1)."""
    grid = score_matrix(exp_home, exp_away, rho)
    home = draw = away = 0.0
    for i in range(MAX_GOALS + 1):
        for j in range(MAX_GOALS + 1):
            p = grid[i][j]
            if i > j:
                home += p
            elif i == j:
                draw += p
            else:
                away += p
    return {"home": round(home, 4), "draw": round(draw, 4), "away": round(away, 4)}


def over_prob(exp_home: float, exp_away: float, line: float = 2.5,
              rho: float = RHO) -> float:
    """P(total goals > line). Summed off the DC grid so it's consistent with the
    1X2 numbers (the low-score tweak shifts a little mass onto draws/unders)."""
    grid = score_matrix(exp_home, exp_away, rho)
    over = 0.0
    for i in range(MAX_GOALS + 1):
        for j in range(MAX_GOALS + 1):
            if i + j > line:
                over += grid[i][j]
    return round(over, 4)


def btts_prob(exp_home: float, exp_away: float, rho: float = RHO) -> float:
    """P(both teams score >= 1)."""
    grid = score_matrix(exp_home, exp_away, rho)
    p = 0.0
    for i in range(1, MAX_GOALS + 1):
        for j in range(1, MAX_GOALS + 1):
            p += grid[i][j]
    return round(p, 4)
