"""NBA player-prop rate model — same design as the WNBA model
(``project547/models/wnba_props``): per-player exponential-recency-weighted,
sample-shrunk rate → negative binomial P(over) for points / rebounds / assists /
threes / PRA, plus the shared opponent-defense adjustment.

Dispersions ``r`` (var = mean + mean^2/r) were fit **walk-forward** on the
committed NBA box-score logs (``data/history/backfill/nba/<season>/…``): for each
player-game we form the recency-weighted shrunk rate from the player's prior
games and take the predictive residual, then set ``r`` by method of moments on
those residuals (n≈6.7k games). This matches the WNBA/NHL methodology — the raw
within-player variance over-states the *predictive* spread once you condition on a
good rate, so a marginal fit would price the tails too wide:

    points r=4.5 · rebounds r=7.0 · assists r=6.0 · threes r=5.5 · pra r=6.4
    (predictive fit: 4.5 / 7.4 / 6.0 / 5.7 / 6.4)

Baselines are per-appearance means among rotation players (>=15 min). Everything
is walk-forward safe and ships behind the demonstrated-edge gate like every
market — surfaced and tracked in probation until it proves CLV.
"""
from __future__ import annotations

from dataclasses import dataclass

# market -> (box-score stat column, NB dispersion r [walk-forward fit], league
#            baseline per-game rate, shrink strength in "prior games").
MARKETS = {
    "points":   dict(stat="points",     r=4.5, base=13.0, prior=5.0),
    "rebounds": dict(stat="rebounds",   r=7.0, base=4.8,  prior=5.0),
    "assists":  dict(stat="assists",    r=6.0, base=3.0,  prior=5.0),
    "threes":   dict(stat="three_made", r=5.5, base=1.5,  prior=6.0),
    "pra":      dict(stat="pra",        r=6.4, base=21.0, prior=5.0),
}
ALIASES = {
    "3-pointers made": "threes", "made threes": "threes", "three_made": "threes",
    "3-pt made": "threes", "threes made": "threes",
    "pts+reb+ast": "pra", "points+rebounds+assists": "pra",
    "player points": "points", "player rebounds": "rebounds",
    "player assists": "assists",
}
HALF_LIFE = 10.0  # games; longer NBA season -> slightly longer recency window
MIN_GAMES = 5

# re-use the shared pure machinery (identical math + opponent engine).
from .wnba_props import (  # noqa: E402
    _nb_cdf, prob_over, weighted_rate,
    defense_factors as _defense_factors, opponent_factor as _opponent_factor,
)


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
    proj: float
    n: int
    r: float


def project(values: list[float], market: str) -> PropProjection | None:
    key = canonical_market(market)
    if key is None:
        return None
    cfg = MARKETS[key]
    rate, _ = weighted_rate([float(v) for v in values], base=cfg["base"],
                            prior=cfg["prior"], half_life=HALF_LIFE)
    return PropProjection(market=key, proj=round(rate, 2), n=len(values),
                          r=cfg["r"])


def defense_factors(rows) -> dict:
    return _defense_factors(rows, MARKETS)


def opponent_factor(market: str, opponent_team: str | None, table: dict) -> float:
    return _opponent_factor(market, opponent_team, table, canonical=canonical_market)
