"""PrizePicks/Underdog-style slip optimizer over our prop projections.

Builds pick candidates from a slate's props (the chosen side's model
probability, capped — the model is overconfident in the tails), then
assembles the highest-EV power/flex slips. Payout tables are PrizePicks'
standard multipliers; Underdog's are close enough that the same slips
apply. EV per $1 = multiplier x P(all hit) for power; flex uses the exact
hit-count distribution (heterogeneous probabilities via DP).
"""

from __future__ import annotations

import pandas as pd

from . import odds

POWER = {2: 3.0, 3: 5.0, 4: 10.0, 5: 20.0, 6: 37.5}
FLEX = {
    3: {3: 2.25, 2: 1.25},
    4: {4: 5.0, 3: 1.5},
    5: {5: 10.0, 4: 2.0, 3: 0.4},
    6: {6: 25.0, 5: 2.0, 4: 0.4},
}
PROB_CAP = 0.72  # don't let one hot projection dominate a slip


def per_leg_breakeven(n: int) -> float | None:
    """Win prob each leg must clear for a power play to break even (all-or-
    nothing payout, independent legs): mult^(-1/n)."""
    m = POWER.get(n)
    return round(m ** (-1.0 / n), 4) if m else None


def _devig(oo, uo) -> float | None:
    """De-vigged 'over' probability from a two-way price, or None."""
    if oo is None or uo is None or pd.isna(oo) or pd.isna(uo):
        return None
    try:
        fo, _ = odds.devig_two_way(odds.implied_prob(oo), odds.implied_prob(uo))
    except Exception:
        return None
    return fo


def _book_prob(p: dict, side: str) -> float | None:
    """De-vigged book probability for the chosen side, from the prop's
    two-way prices. This is the 'market' we must beat — DFS lines that are
    softer than this are the edge."""
    fo = _devig(p.get("over_odds"), p.get("under_odds"))
    if fo is None:
        return None
    return fo if side == "Over" else 1 - fo


# DFS operators we surface their own pick'em line for, in preference order.
_DFS_SOURCES = (("pp", "PrizePicks"), ("ud", "Underdog"))


def _dfs_legs(p: dict, cap: float) -> list[dict]:
    """Candidate rows priced off each DFS operator's *own* pick'em line (and our
    model prob at that line) — what you actually wager on PrizePicks/Underdog.
    The model prob of clearing the (often softer) DFS number, vs the DFS price,
    is the real edge. Returns one leg per operator that quotes this prop."""
    legs = []
    for prefix, label in _DFS_SOURCES:
        line = p.get(f"{prefix}_line")
        mop = p.get(f"{prefix}_model_over_prob")
        if line is None or pd.isna(line) or mop is None or pd.isna(mop):
            continue
        side = "Over" if mop >= 0.5 else "Under"
        raw = float(max(mop, 1 - mop))
        fo = _devig(p.get(f"{prefix}_over_odds"), p.get(f"{prefix}_under_odds"))
        bp = (fo if side == "Over" else 1 - fo) if fo is not None else None
        legs.append({
            "player": p.get("player"), "team": p.get("team") or "",
            "market": p.get("market"), "line": float(line), "side": side,
            "prob": round(min(raw, cap), 4), "raw_prob": round(raw, 4),
            "book_prob": round(bp, 4) if bp is not None else None,
            "edge": round(raw - bp, 4) if bp is not None else None,
            "line_source": label,
        })
    return legs


def candidates(day_slates: dict, cap: float = PROB_CAP,
               dfs_only: bool = False) -> pd.DataFrame:
    """One row per prop: the better side, its (capped) model probability, the
    de-vigged book probability, and our edge over the market.

    When an operator's own pick'em line is present (pp_/ud_ columns), the prop is
    priced off that DFS line instead of the consensus — that's the number you
    actually bet. ``dfs_only`` drops props with no DFS line at all."""
    rows = []
    for sport, blob in (day_slates or {}).items():
        for p in blob.get("props", []) or []:
            dfs_legs = _dfs_legs(p, cap)
            if dfs_legs:
                for leg in dfs_legs:
                    rows.append({"sport": sport, **leg})
                continue
            if dfs_only:
                continue
            mop = p.get("model_over_prob")
            line = p.get("line")
            if mop is None or pd.isna(mop) or line is None or pd.isna(line):
                continue
            side = "Over" if mop >= 0.5 else "Under"
            raw = float(max(mop, 1 - mop))
            bp = _book_prob(p, side)
            rows.append({
                "sport": sport, "player": p.get("player"),
                "team": p.get("team") or "", "market": p.get("market"),
                "line": float(line), "side": side,
                "prob": round(min(raw, cap), 4), "raw_prob": round(raw, 4),
                "book_prob": round(bp, 4) if bp is not None else None,
                "edge": round(raw - bp, 4) if bp is not None else None,
                "line_source": "consensus",
            })
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    # one pick per player (their best edge, then probability) — picks the better
    # DFS operator when both quote the same player.
    df = df.sort_values(["edge", "prob"], ascending=False,
                        na_position="last").drop_duplicates("player")
    return df.reset_index(drop=True)


def hit_distribution(probs: list[float]) -> list[float]:
    """P(exactly k hits) for independent legs with given probabilities."""
    dist = [1.0]
    for p in probs:
        nxt = [0.0] * (len(dist) + 1)
        for k, q in enumerate(dist):
            nxt[k] += q * (1 - p)
            nxt[k + 1] += q * p
        dist = nxt
    return dist


def slip_evs(probs: list[float]) -> dict:
    """{power_ev, flex_ev, joint} per $1 staked for these legs."""
    n = len(probs)
    dist = hit_distribution(probs)
    joint = dist[n]
    power = POWER.get(n)
    flex_table = FLEX.get(n)
    flex = (sum(dist[k] * m for k, m in flex_table.items())
            if flex_table else None)
    return {"joint": round(joint, 4),
            "power_ev": round(power * joint - 1, 4) if power else None,
            "flex_ev": round(flex - 1, 4) if flex is not None else None}


def best_slips(cands: pd.DataFrame, max_per_team: int = 2,
               min_edge: float = 0.0) -> list[dict]:
    """Greedy slips for sizes 2-6 from the legs where our model most beats the
    market. Only legs with edge >= min_edge over the de-vigged book price are
    eligible (no edge -> PrizePicks prices it the same -> no value)."""
    if cands is None or cands.empty:
        return []
    pool = cands[cands["edge"].notna() & (cands["edge"] >= min_edge)]
    pool = pool.sort_values(["prob", "edge"], ascending=False)
    legs, team_counts = [], {}
    for _, r in pool.iterrows():
        t = r["team"]
        if team_counts.get(t, 0) >= max_per_team and t:
            continue
        team_counts[t] = team_counts.get(t, 0) + 1
        legs.append(r.to_dict())
        if len(legs) >= 6:
            break
    out = []
    for n in (2, 3, 4, 5, 6):
        if len(legs) < n:
            break
        chosen = legs[:n]
        evs = slip_evs([l["prob"] for l in chosen])
        out.append({"size": n, "legs": chosen,
                    "breakeven": per_leg_breakeven(n), **evs})
    return out
