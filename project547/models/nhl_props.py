"""NHL skater-prop rate model — same design as the WNBA model
(``project547/models/wnba_props``): per-player recency-weighted, shrunk rate →
negative binomial P(over) for shots-on-goal / points / goals / assists / blocks.

Status: the distribution dispersions below are **provisional priors** based on
typical NHL skater rates, NOT yet fit to data — the committed skater logs don't
exist until the box-score CSV is imported (``scripts/import_nhl_skaters.py``).
Once ``data/history/backfill/nhl/<year>/player_games.jsonl.gz`` lands,
``scripts/validate_nhl_props.py`` fits each market's ``r`` on a held-out split
and checks calibration (exactly as WNBA was done, ECE 0.010); only then does the
model get wired into the live pipeline behind the edge gate. Until validated it
is not trusted over the vendor projection.
"""
from __future__ import annotations

from dataclasses import dataclass

# market -> (box-score stat column, provisional NB dispersion r, league
#            baseline per-game rate, shrink strength in "prior games").
# r values are placeholders pending the fit; goals/assists/points are low-mean
# and near-Poisson (high r), shots is the workhorse count.
MARKETS = {
    "shots":   dict(stat="shots",   r=6.0, base=2.2, prior=5.0),
    "points":  dict(stat="points",  r=3.0, base=0.6, prior=6.0),
    "goals":   dict(stat="goals",   r=4.0, base=0.3, prior=6.0),
    "assists": dict(stat="assists", r=4.0, base=0.4, prior=6.0),
    "blocks":  dict(stat="blocks",  r=4.0, base=1.0, prior=6.0),
}
ALIASES = {
    "shots on goal": "shots", "sog": "shots", "player shots": "shots",
    "player points": "points", "player goals": "goals",
    "player assists": "assists", "blocked shots": "blocks",
}
HALF_LIFE = 10.0   # games; NHL skater form is noisier -> slightly longer window
MIN_GAMES = 5

# re-use the pure WNBA machinery (identical math) so there is one implementation.
from .wnba_props import _nb_cdf, prob_over, weighted_rate  # noqa: E402


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
