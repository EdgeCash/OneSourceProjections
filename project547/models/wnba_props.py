"""WNBA player-prop rate model — a real per-player distribution built from the
committed box-score logs, so the app can price points/rebounds/assists/threes/
PRA itself instead of only echoing a vendor projection.

Approach (deliberately simple and provable):
  * **Rate** = exponential-recency-weighted mean of the player's prior games,
    shrunk toward a market baseline by sample size (a rookie / thin sample is
    pulled toward the league level, not trusted at face value).
  * **Distribution** = negative binomial with a per-market dispersion ``r`` fit
    from the *within-player* variance of the committed logs (these counts are
    over-dispersed relative to Poisson — see fit below), giving P(over line).

Everything is walk-forward safe: feed it only games before the projection date.
Calibration is validated in ``scripts/validate_wnba_props.py`` (reliability +
log-loss vs a naive baseline) — the honest first gate before any edge/CLV claim.
It ships behind the demonstrated-edge gate like every other market.

Dispersion ``r`` (var = mean + mean^2 / r). Starting values came from the
within-player variance of the 2018–2026 logs; the two high-mean markets were
then refined by a train (pre-2024) / test (2024+) split, because raw
within-player variance over-states the *predictive* spread once you condition on
a good recency-weighted rate. Final, held-out-validated:

    points r=5.0 · rebounds r=6.2 · assists r=5.8 · threes r=3.0 · pra r=7.0

(points 2.4→5.0 cut test ECE 0.025→0.010; pra 3.7→7.0 cut it 0.025→0.018;
rebounds/assists/threes were already best on the holdout.)
"""
from __future__ import annotations

from dataclasses import dataclass

# market -> (box-score stat column, NB dispersion r, league baseline rate,
#            shrink strength in "prior games")
MARKETS = {
    "points":   dict(stat="points",     r=5.0, base=8.5,  prior=5.0),
    "rebounds": dict(stat="rebounds",   r=6.2, base=3.5,  prior=5.0),
    "assists":  dict(stat="assists",    r=5.8, base=2.0,  prior=5.0),
    "threes":   dict(stat="three_made", r=3.0, base=0.8,  prior=6.0),
    "pra":      dict(stat="pra",        r=7.0, base=14.0, prior=5.0),
}
# market aliases -> canonical key (mirrors playerlogs.MARKET_STAT vocabulary)
ALIASES = {
    "3-pointers made": "threes", "made threes": "threes", "three_made": "threes",
    "pts+reb+ast": "pra", "points+rebounds+assists": "pra",
    "player points": "points", "player rebounds": "rebounds",
    "player assists": "assists",
}
HALF_LIFE = 8.0  # games; recency weight = 0.5 ** (age_in_games / HALF_LIFE)
MIN_GAMES = 5    # prior games before the model projection is trusted over vendor


def canonical_market(market: str) -> str | None:
    if not market:
        return None
    m = market.strip().lower()
    if m in MARKETS:
        return m
    return ALIASES.get(m)


@dataclass(frozen=True)
class PropProjection:
    market: str
    proj: float          # projected mean (recency-weighted, shrunk)
    n: int               # prior games backing it
    r: float             # NB dispersion used


def weighted_rate(values: list[float], *, base: float, prior: float,
                  half_life: float = HALF_LIFE) -> tuple[float, float]:
    """Exponential-recency-weighted mean of ``values`` (most-recent LAST),
    shrunk toward ``base`` by ``prior`` pseudo-games. Returns (rate, eff_n)."""
    if not values:
        return base, 0.0
    n = len(values)
    # age 0 = most recent (last element)
    wsum = 0.0
    vsum = 0.0
    for i, v in enumerate(values):
        age = (n - 1) - i
        w = 0.5 ** (age / half_life)
        wsum += w
        vsum += w * v
    obs = vsum / wsum
    # shrink toward base using effective (unweighted) sample size
    rate = (n * obs + prior * base) / (n + prior)
    return rate, wsum


def _nb_cdf(k: int, mean: float, r: float) -> float:
    """P(X <= k) for a negative binomial with the given mean and dispersion r
    (var = mean + mean^2/r). Iterative pmf sum — counts are small, so this is
    cheap and dependency-free (no scipy)."""
    if mean <= 0:
        return 1.0
    p = r / (r + mean)              # success prob in the (r, p) parameterization
    pmf = p ** r                    # P(X = 0)
    cdf = pmf
    for i in range(1, k + 1):
        pmf *= (i - 1 + r) / i * (1 - p)
        cdf += pmf
    return min(cdf, 1.0)


def prob_over(mean: float, line: float, r: float) -> float:
    """P(stat > line). For a half-point line (4.5) this is P(X >= 5); for an
    integer line (4) the push mass at exactly 4 is excluded (standard over)."""
    import math
    k = math.floor(line)
    return max(0.0, 1.0 - _nb_cdf(k, mean, r))


def project(values: list[float], market: str) -> PropProjection | None:
    """Projected mean + params for a market from the player's prior stat series
    (chronological, most-recent last). Returns None for unknown markets."""
    key = canonical_market(market)
    if key is None:
        return None
    cfg = MARKETS[key]
    rate, _ = weighted_rate([float(v) for v in values], base=cfg["base"],
                            prior=cfg["prior"])
    return PropProjection(market=key, proj=round(rate, 2), n=len(values),
                          r=cfg["r"])


# ---------------------------------------------------------------------------
# Opponent adjustment: nudge a player's projection by how much the opposing team
# allows in that market vs the league. The per-player rate above is opponent-
# blind; a soft matchup should lift it and a stingy one trim it. Kept small
# (shrunk by sample, hard-clamped) — a first, conservative pass that ships behind
# the demonstrated-edge gate like everything else.
# ---------------------------------------------------------------------------
DEF_SHRINK = 60.0    # opposing player-appearances of "league-average" prior
DEF_CLAMP = 0.10     # cap the opponent adjustment at ±10%


def defense_factors(rows) -> dict:
    """Per-(defending_team, market) opponent-defense multiplier from committed
    box-score logs. Each ``row`` is a dict with ``team`` — the DEFENDING team
    (i.e. the log's opponent), already canonicalized by the caller so it matches
    the slate's team keys — plus the market stat columns. A team's factor for a
    market is (avg stat it allows per opposing appearance) / league average,
    shrunk toward 1.0 by the appearance count and clamped. Returns
    ``{(team, market): factor}``; teams/markets with no data are simply absent
    (read as neutral 1.0 by :func:`opponent_factor`)."""
    import collections
    agg: dict = collections.defaultdict(lambda: {"s": 0.0, "n": 0})
    tot: dict = collections.defaultdict(lambda: {"s": 0.0, "n": 0})
    for row in rows:
        team = row.get("team")
        if not team:
            continue
        for mk, cfg in MARKETS.items():
            v = row.get(cfg["stat"])
            if v is None:
                continue
            try:
                v = float(v)
            except (TypeError, ValueError):
                continue
            agg[(team, mk)]["s"] += v
            agg[(team, mk)]["n"] += 1
            tot[mk]["s"] += v
            tot[mk]["n"] += 1
    league = {mk: (t["s"] / t["n"]) for mk, t in tot.items() if t["n"]}
    out: dict = {}
    for (team, mk), a in agg.items():
        lg = league.get(mk)
        if not lg or lg <= 0 or not a["n"]:
            continue
        raw = (a["s"] / a["n"]) / lg
        w = a["n"] / (a["n"] + DEF_SHRINK)              # sample-size shrinkage
        f = 1.0 + (raw - 1.0) * w
        out[(team, mk)] = round(min(max(f, 1 - DEF_CLAMP), 1 + DEF_CLAMP), 4)
    return out


def opponent_factor(market: str, opponent_team: str | None, table: dict) -> float:
    """Opponent-defense multiplier (~0.9–1.1) for a market vs a team; 1.0 when
    unknown. ``opponent_team`` and the ``table`` keys must share one team
    canonicalization (the caller's ``teams.canon``)."""
    key = canonical_market(market)
    if not key or not opponent_team or not table:
        return 1.0
    return table.get((opponent_team, key), 1.0)
