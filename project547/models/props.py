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


# Per-game total bases are heavily overdispersed (var/mean ~2.2): lots of
# 0-TB games plus occasional extra-base spikes. A plain Poisson on the mean
# over-projects P(over); a negative binomial with this size matches the
# observed distribution (backtest calibration gap ~0 at 1.0-1.1).
TB_DISPERSION = 1.1


def prob_over_neg_binom(mean: float, line: float, dispersion: float = TB_DISPERSION) -> float:
    """P(X > line) for a negative-binomial X with the given mean and size
    (dispersion). Used for total bases and other overdispersed counts."""
    mean = max(mean, 1e-6)
    p = dispersion / (dispersion + mean)
    return float(1 - stats.nbinom.cdf(int(line), dispersion, p))


def refine_expected_innings(base: float, workload: dict | None) -> float:
    """Nudge the season-average expected innings toward what a pitcher's recent
    pitch counts actually support. ``base`` is the season IP/GS expectation;
    ``workload`` is clients.mlb_statsapi.pitcher_recent_workload(). A starter
    building up from the IL or on a pitch limit throws fewer innings than his
    season line implies — the pitch ceiling catches that; the season average
    lags. Symmetric but conservative: the recent budget gets half the weight,
    and it can only lift ``base`` by up to half an inning (managers pull
    starters for many reasons, so we trust the downside more than the upside).
    Returns ``base`` unchanged when there's no usable recent workload."""
    if not workload or workload.get("n_starts", 0) < 2:
        return base
    ppo = workload.get("pitches_per_out") or 0
    ceiling = workload.get("pitch_ceiling") or 0
    if ppo <= 0 or ceiling <= 0:
        return base
    budget_innings = (ceiling / ppo) / 3.0     # innings the ceiling supports
    exp = 0.5 * base + 0.5 * budget_innings
    exp = min(exp, base + 0.5)                  # cap the upside
    return float(max(3.0, min(exp, 7.5)))


def pitcher_strikeouts(
    expected_innings: float,
    k_rate: float | None,
    opp_k_rate: float | None = None,
    fp_projected_k: float | None = None,
    ump_k_factor: float = 1.0,
) -> dict:
    """Expected Ks and a function-ready lambda for the Poisson.

    opp_k_rate shifts the matchup: a team that strikes out 26% of the time
    inflates lambda vs one at 18%. ump_k_factor applies the home-plate umpire's
    (shrunk, clamped) strikeout-zone tendency; 1.0 = neutral.
    """
    rate = blend(k_rate, None, LEAGUE_K_RATE)
    if opp_k_rate:
        rate = rate * (0.5 + 0.5 * opp_k_rate / LEAGUE_K_RATE)
    lam_own = expected_innings * BF_PER_INNING * rate
    lam = blend(lam_own, fp_projected_k, lam_own)
    if ump_k_factor and ump_k_factor != 1.0:
        lam = lam * ump_k_factor
    return {"lambda": lam, "mean": lam}


# Additional pitcher markets (DFS books quote all of these). Means come from
# expected innings × a per-inning rate (own rate when available, else league),
# blended with the FantasyPros daily projection. Dispersions are first-pass
# priors — overdispersed counts, lowest for the spiky earned-runs line — to be
# tightened against the prop-calibration backtest as forward data accrues.
LEAGUE_H_PER_INNING = 0.92     # ~8.3 H/9
LEAGUE_ER_PER_INNING = 0.45    # ~4.05 ERA
LEAGUE_BB_PER_INNING = 0.32    # ~2.9 BB/9
OUTS_DISPERSION = 40.0         # outs ~ mean 16, only mildly overdispersed
HITS_ALLOWED_DISPERSION = 7.0
ER_DISPERSION = 3.0            # earned runs are the spikiest (blowup starts)
WALKS_DISPERSION = 4.0


def pitcher_outs(expected_innings: float,
                 fp_projected_outs: float | None = None) -> dict:
    """Outs recorded = innings × 3, blended with any FP outs projection."""
    own = expected_innings * 3.0
    mean = blend(own, fp_projected_outs, own)
    return {"mean": mean, "dispersion": OUTS_DISPERSION}


def pitcher_hits_allowed(expected_innings: float,
                         h_per_inning: float | None = None,
                         fp_projected_h: float | None = None) -> dict:
    own = expected_innings * (h_per_inning if h_per_inning else LEAGUE_H_PER_INNING)
    mean = blend(own, fp_projected_h, own)
    return {"mean": mean, "dispersion": HITS_ALLOWED_DISPERSION}


def pitcher_earned_runs(expected_innings: float,
                        er_per_inning: float | None = None,
                        fp_projected_er: float | None = None) -> dict:
    own = expected_innings * (er_per_inning if er_per_inning else LEAGUE_ER_PER_INNING)
    mean = blend(own, fp_projected_er, own)
    return {"mean": mean, "dispersion": ER_DISPERSION}


def pitcher_walks(expected_innings: float,
                  bb_per_inning: float | None = None,
                  fp_projected_bb: float | None = None) -> dict:
    own = expected_innings * (bb_per_inning if bb_per_inning else LEAGUE_BB_PER_INNING)
    mean = blend(own, fp_projected_bb, own)
    return {"mean": mean, "dispersion": WALKS_DISPERSION}


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


# Per-game hits are overdispersed relative to a fixed-n binomial: actual at-bats
# vary game to game (walks, pinch-hits, early exits) and BABIP swings, so a plain
# binomial understates low-hit games and over-projects P(over). A beta-binomial
# with this concentration adds that variance; tuned on the 2024-25 calibration
# (batter_hits gap +0.026 -> ~0).
HITS_CONCENTRATION = 11.0


def prob_over_hits(expected_ab: float, p: float, line: float,
                   concentration: float = HITS_CONCENTRATION) -> float:
    n = max(1, round(expected_ab))
    a = concentration * p
    b = concentration * (1 - p)
    return float(1 - stats.betabinom.cdf(int(line), n, a, b))


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
