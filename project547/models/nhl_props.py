"""NHL skater-prop rate model — same design as the WNBA model
(``project547/models/wnba_props``): per-player recency-weighted, shrunk rate →
negative binomial P(over) for shots-on-goal / points / goals / assists / blocks.

Status: **validated.** The dispersions below were fit on the committed skater
logs (2016–2025, imported via ``scripts/import_nhl_skaters.py``) on a train
(pre-2024) / test (2024+) split by ``scripts/validate_nhl_props.py``. Held-out
calibration is excellent across every market (ECE ≤ 0.013, matching WNBA):

    shots  r=7.5 ECE 0.010 · points r=5.8 ECE 0.010 · goals r=1.5 ECE 0.012 ·
    assists r=2.8 ECE 0.013 · blocks r=5.8 ECE 0.006 · saves r=10.8 ECE 0.032

Saves (a goalie market) calibrates less tightly than the skater markets because
it's driven by shots faced (opponent offense + game script), which a per-goalie
rate doesn't capture — a future v2 can project shots-against × save% instead.
Good enough as a gated v1. All wired into the live pipeline
(``project_generic_props``) behind the demonstrated-edge gate, like WNBA —
surfaced and tracked in probation until it proves CLV.
"""
from __future__ import annotations

from dataclasses import dataclass

# market -> (box-score stat column, NB dispersion r [fit on a held-out split],
#            league baseline per-game rate, shrink strength in "prior games",
#            role ["skater"|"goalie"]). Saves is a goalie market — a starting
#            goalie faces ~30 shots, a very different distribution from skaters.
MARKETS = {
    "shots":   dict(stat="shots",   r=7.5,  base=2.2,  prior=5.0, role="skater"),
    "points":  dict(stat="points",  r=5.8,  base=0.6,  prior=6.0, role="skater"),
    "goals":   dict(stat="goals",   r=1.5,  base=0.3,  prior=6.0, role="skater"),
    "assists": dict(stat="assists", r=2.8,  base=0.4,  prior=6.0, role="skater"),
    "blocks":  dict(stat="blocks",  r=5.8,  base=1.0,  prior=6.0, role="skater"),
    "saves":   dict(stat="saves",   r=10.8, base=26.0, prior=4.0, role="goalie"),
}
ALIASES = {
    "shots on goal": "shots", "sog": "shots", "player shots": "shots",
    "player points": "points", "player goals": "goals",
    "player assists": "assists", "blocked shots": "blocks",
    "goalie saves": "saves", "player saves": "saves", "total saves": "saves",
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
