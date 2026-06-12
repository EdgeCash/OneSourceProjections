"""Player prop models. Each returns P(over) for a given line.

Distributional choices:
  - Pitcher strikeouts: Poisson(lambda = expected BF * K%)
  - Batter hits: Binomial(AB, per-AB hit prob) — xBA-informed
  - Batter total bases: Poisson(lambda = AB * expected TB per AB)
  - Batter home runs: P(>=1) from per-PA HR rate

Rates are blended: our Statcast/FanGraphs-derived rate gets
(1 - FP_BLEND_WEIGHT), the FantasyPros projection-implied rate gets
FP_BLEND_WEIGHT, when both are available.
"""

from __future__ import annotations

from scipy import stats

from .. import config

LEAGUE_K_RATE = 0.222          # per-PA strikeout rate
LEAGUE_BA = 0.244
LEAGUE_TB_PER_AB = 0.408       # ~league SLG
LEAGUE_HR_PER_PA = 0.031
BF_PER_INNING = 4.25


def blend(own: float | None, fp: float | None, league: float) -> float:
    """Combine our rate with the FantasyPros-implied rate; fall back to
    whichever exists, then to league average."""
    if own is not None and fp is not None:
        w = config.FP_BLEND_WEIGHT
        return w * fp + (1 - w) * own
    return own if own is not None else (fp if fp is not None else league)


def prob_over_count(lam: float, line: float) -> float:
    """P(X > line) for Poisson X. Works for half lines (5.5) and whole
    lines (6 → strictly over; pushes are handled by the caller's odds)."""
    return float(1 - stats.poisson.cdf(int(line), lam))


def pitcher_strikeouts(
    expected_innings: float,
    k_rate: float | None,
    opp_k_rate: float | None = None,
    fp_projected_k: float | None = None,
) -> dict:
    """Expected Ks and a function-ready lambda for the Poisson.

    opp_k_rate shifts the matchup: a team that strikes out 26% of the time
    inflates lambda vs one at 18%.
    """
    rate = blend(k_rate, None, LEAGUE_K_RATE)
    if opp_k_rate:
        rate = rate * (0.5 + 0.5 * opp_k_rate / LEAGUE_K_RATE)
    lam_own = expected_innings * BF_PER_INNING * rate
    lam = blend(lam_own, fp_projected_k, lam_own)
    return {"lambda": lam, "mean": lam}


def batter_hits(
    expected_ab: float,
    ba: float | None,
    xba: float | None = None,
    fp_projected_h: float | None = None,
) -> dict:
    """Per-AB hit probability uses 60/40 xBA/BA when Statcast xBA exists
    (xBA is stickier than realized BA over partial seasons)."""
    if ba is not None and xba is not None:
        p_own = 0.6 * xba + 0.4 * ba
    else:
        p_own = xba if xba is not None else ba
    fp_rate = (fp_projected_h / expected_ab) if fp_projected_h else None
    p = blend(p_own, fp_rate, LEAGUE_BA)
    return {"n": expected_ab, "p": p, "mean": expected_ab * p}


def prob_over_hits(expected_ab: float, p: float, line: float) -> float:
    n = max(1, round(expected_ab))
    return float(1 - stats.binom.cdf(int(line), n, p))


def batter_total_bases(
    expected_ab: float,
    slg: float | None,
    xslg: float | None = None,
    fp_projected_tb: float | None = None,
) -> dict:
    if slg is not None and xslg is not None:
        rate_own = 0.6 * xslg + 0.4 * slg
    else:
        rate_own = xslg if xslg is not None else slg
    fp_rate = (fp_projected_tb / expected_ab) if fp_projected_tb else None
    rate = blend(rate_own, fp_rate, LEAGUE_TB_PER_AB)
    lam = expected_ab * rate
    return {"lambda": lam, "mean": lam}


def batter_home_run(
    expected_pa: float,
    hr_per_pa: float | None,
    fp_projected_hr: float | None = None,
) -> dict:
    fp_rate = (fp_projected_hr / expected_pa) if fp_projected_hr else None
    rate = blend(hr_per_pa, fp_rate, LEAGUE_HR_PER_PA)
    p_at_least_one = 1 - (1 - rate) ** expected_pa
    return {"p_hr": p_at_least_one, "rate": rate}


def expected_ab_for_slot(slot: int) -> float:
    """Lineup slot 1-9 → typical AB per game (top of order bats more)."""
    return {1: 4.4, 2: 4.3, 3: 4.2, 4: 4.1, 5: 4.0, 6: 3.9, 7: 3.8, 8: 3.7, 9: 3.6}.get(
        slot, 3.9
    )
