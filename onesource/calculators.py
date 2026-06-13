"""Betting calculators — the deterministic toolkit every research site ships.

All pure functions over odds math (onesource.odds), so they're unit-tested
without the dashboard. American odds in, plain dicts/floats out.
"""

from __future__ import annotations

import numpy as np

from . import odds


# ---------------------------------------------------------------------------
# De-vig / fair odds
# ---------------------------------------------------------------------------

def hold(*americans: float) -> float:
    """Bookmaker margin (overround) of a market: Σ implied prob − 1."""
    return sum(odds.implied_prob(a) for a in americans) - 1.0


def no_vig(*americans: float, method: str = "multiplicative") -> list[float]:
    """Fair (de-vigged) probabilities for a 2+ way market.

    'multiplicative' normalizes raw implied probs proportionally; 'power'
    solves Σ pᵢ^k = 1 (corrects favorite-longshot bias, stays in [0,1]) —
    the method sharp bettors prefer.
    """
    imp = [odds.implied_prob(a) for a in americans]
    if method == "power":
        lo, hi = 0.5, 5.0
        for _ in range(60):
            k = (lo + hi) / 2
            if sum(p ** k for p in imp) > 1:
                lo = k
            else:
                hi = k
        k = (lo + hi) / 2
        return [p ** k for p in imp]
    total = sum(imp)
    return [p / total for p in imp]


# ---------------------------------------------------------------------------
# Arbitrage / hedge / middle
# ---------------------------------------------------------------------------

def arbitrage(americans: list[float], total: float = 100.0) -> dict | None:
    """Detect a sure-bet across the best price per outcome. Returns per-side
    stakes + guaranteed profit, or None if no arb (Σ 1/decimal ≥ 1)."""
    decs = [odds.american_to_decimal(a) for a in americans]
    s = sum(1 / d for d in decs)
    if s >= 1:
        return None
    stakes = [round(total * (1 / d) / s, 2) for d in decs]
    payout = total / s
    return {"profit_pct": round((1 / s - 1) * 100, 3),
            "profit": round(payout - total, 2),
            "stakes": stakes, "total": total}


def hedge(orig_stake: float, orig_american: float, hedge_american: float) -> dict:
    """Stake on the other side to lock equal profit regardless of result."""
    orig_return = orig_stake * odds.american_to_decimal(orig_american)
    hedge_dec = odds.american_to_decimal(hedge_american)
    hedge_stake = orig_return / hedge_dec
    guaranteed = orig_return - (orig_stake + hedge_stake)
    return {"hedge_stake": round(hedge_stake, 2),
            "guaranteed_profit": round(guaranteed, 2),
            "total_outlay": round(orig_stake + hedge_stake, 2)}


def middle_breakeven(american: float = -110) -> float:
    """Hit rate a middle must clear to break even at the given vig on the two
    sides (you lose the vig on the missing side, win both when it lands)."""
    dec = odds.american_to_decimal(american)
    # win = dec-1 on both legs (≈2·(dec-1)); miss = lose the vig on one leg.
    win, loss = 2 * (dec - 1), (2 - dec)
    return round(loss / (win + loss), 4) if (win + loss) else 0.0


# ---------------------------------------------------------------------------
# Staking
# ---------------------------------------------------------------------------

def risk_of_ruin(model_prob: float, american: float, fraction: float = 0.25,
                 ruin_fraction: float = 0.5, n_bets: int = 500,
                 sims: int = 4000, seed: int = 7) -> float:
    """Monte-Carlo probability of a drawdown to ``ruin_fraction`` of the
    starting bankroll over ``n_bets`` proportional fractional-Kelly bets."""
    b = odds.american_to_decimal(american) - 1
    f = odds.kelly_stake(model_prob, american, fraction)
    if f <= 0:
        return 1.0  # no edge -> bankroll bleeds out
    rng = np.random.default_rng(seed)
    banks = np.ones(sims)
    ruined = np.zeros(sims, dtype=bool)
    thresh = ruin_fraction
    for _ in range(n_bets):
        stake = f * banks
        win = rng.random(sims) < model_prob
        banks = np.where(win, banks + stake * b, banks - stake)
        ruined |= banks <= thresh
    return round(float(ruined.mean()), 4)


# ---------------------------------------------------------------------------
# Parlays / correlation
# ---------------------------------------------------------------------------

def parlay(americans: list[float]) -> dict:
    """Independent-leg parlay: combined decimal/American odds and the implied
    (no-vig-free) win probability from each leg's raw implied prob."""
    dec = 1.0
    prob = 1.0
    for a in americans:
        dec *= odds.american_to_decimal(a)
        prob *= odds.implied_prob(a)
    return {"decimal": round(dec, 4), "american": odds.decimal_to_american(dec),
            "implied_prob": round(prob, 4)}


def correlated_two_leg(p_a: float, p_b: float, rho: float) -> dict:
    """Joint probability of two correlated binary legs and its fair price.
    P(A∩B) = P(A)P(B) + ρ·√[P(A)(1−P(A))P(B)(1−P(B))]; ρ=0 → independent."""
    cov = rho * (p_a * (1 - p_a) * p_b * (1 - p_b)) ** 0.5
    joint = min(1.0, max(0.0, p_a * p_b + cov))
    indep = p_a * p_b
    return {"joint_prob": round(joint, 4),
            "independent_prob": round(indep, 4),
            "fair_american": odds.decimal_to_american(1 / joint) if joint > 0 else None}
