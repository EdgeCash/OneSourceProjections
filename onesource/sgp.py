"""Same-game parlay (SGP) pricing with correlation — the Props.cash idea.

Books price same-game parlays by adjusting for correlation between legs. Two
legs that move together (a QB's passing yards and his top receiver's yards) hit
together more often than independence implies, so the true joint probability is
higher than the naive product — and an SGP quoted as if the legs were
independent is too generous (+EV for you). Negatively-correlated legs (two
running backs splitting carries) hit together less often, so an "independent"
quote is too expensive.

We don't have per-player joint histories wired in, so we use well-established
correlation **priors** for common same-game relationships and run them through
the Gaussian-copula joint-probability model in ``onesource.calculators``. Given
each leg's win probability and a correlation, ``price_sgp`` returns the true
joint probability, the fair SGP price, the naive independent price, the
correlation "lift", and — if you pass the book's quoted SGP odds — the EV and
quarter-Kelly stake of taking that quote against the correlated fair value.
"""

from __future__ import annotations

from . import calculators, odds

# Typical correlation priors for common same-game leg relationships. These are
# practitioner rules of thumb (the sign and rough magnitude matter far more than
# the second decimal); override with the manual control when you have a read.
CORRELATION_PRESETS: dict[str, float] = {
    "Unrelated / different games (independent)": 0.0,
    "Same team, same direction (team total over + player over)": 0.25,
    "QB passing over ↔ his receiver's yards/TDs over": 0.45,
    "Player points over ↔ his team total over": 0.35,
    "Game total over ↔ both teams' scorers over": 0.30,
    "Pitcher strikeouts over ↔ opposing team total under": 0.30,
    "RB rush yards over ↔ his team moneyline (game script)": 0.30,
    "Teammates competing for the same usage (negative)": -0.20,
    "Opposing players / hedge legs (negative)": -0.30,
}


def price_sgp(probs: list[float], rho: float = 0.0,
              quoted_american: float | None = None) -> dict:
    """Price a same-game parlay under correlation ``rho``.

    ``probs`` are the per-leg true win probabilities (e.g. our model's prob on
    each side). Two legs use the exact correlated-pair formula; three or more
    use the equicorrelation Gaussian copula (so ``rho`` is treated as the
    common pairwise correlation). Returns the correlated ``joint_prob``, the
    naive ``independent_prob``, the ``fair_american`` and ``independent_american``
    prices, and the ``lift`` (joint − independent). If ``quoted_american`` is
    given, adds ``quoted_prob`` (its raw implied prob), ``ev`` (per unit at that
    price vs the correlated fair value), and a ¼-Kelly ``stake`` fraction.
    """
    probs = [float(p) for p in probs if p is not None]
    if len(probs) < 2:
        raise ValueError("an SGP needs at least two legs")
    if not all(0.0 < p < 1.0 for p in probs):
        raise ValueError("each leg probability must be in (0, 1)")

    if len(probs) == 2:
        res = calculators.correlated_two_leg(probs[0], probs[1], rho)
        joint, indep = res["joint_prob"], res["independent_prob"]
        fair = res["fair_american"]
        lift = round(joint - indep, 4)
    else:
        res = calculators.correlated_parlay(probs, rho)
        joint, indep = res["joint_prob"], res["independent_prob"]
        fair, lift = res["fair_american"], res["lift"]

    out = {
        "n_legs": len(probs), "rho": rho,
        "joint_prob": joint, "independent_prob": indep,
        "fair_american": fair,
        "independent_american": (odds.decimal_to_american(1 / indep)
                                 if indep > 0 else None),
        "lift": lift,
    }
    if quoted_american is not None:
        out["quoted_american"] = quoted_american
        out["quoted_prob"] = round(odds.implied_prob(quoted_american), 4)
        out["ev"] = round(odds.expected_value(joint, quoted_american), 4)
        out["stake"] = round(odds.kelly_stake(joint, quoted_american, 0.25), 4)
    return out
