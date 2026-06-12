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

POWER = {2: 3.0, 3: 5.0, 4: 10.0, 5: 20.0, 6: 37.5}
FLEX = {
    3: {3: 2.25, 2: 1.25},
    4: {4: 5.0, 3: 1.5},
    5: {5: 10.0, 4: 2.0, 3: 0.4},
    6: {6: 25.0, 5: 2.0, 4: 0.4},
}
PROB_CAP = 0.72  # don't let one hot projection dominate a slip


def candidates(day_slates: dict, cap: float = PROB_CAP) -> pd.DataFrame:
    """One row per prop: the better side and its (capped) probability."""
    rows = []
    for sport, blob in (day_slates or {}).items():
        for p in blob.get("props", []) or []:
            mop = p.get("model_over_prob")
            line = p.get("line")
            if mop is None or pd.isna(mop) or line is None or pd.isna(line):
                continue
            side = "Over" if mop >= 0.5 else "Under"
            prob = min(float(mop if mop >= 0.5 else 1 - mop), cap)
            rows.append({
                "sport": sport, "player": p.get("player"),
                "team": p.get("team") or "", "market": p.get("market"),
                "line": float(line), "side": side, "prob": round(prob, 4),
                "raw_prob": round(float(max(mop, 1 - mop)), 4),
            })
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    # one pick per player (their best market), modest team concentration
    df = df.sort_values("prob", ascending=False).drop_duplicates("player")
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


def best_slips(cands: pd.DataFrame, max_per_team: int = 2) -> list[dict]:
    """Greedy top-probability slips for sizes 2-6 (legs are the highest-
    probability picks subject to player/team caps)."""
    if cands is None or cands.empty:
        return []
    legs, team_counts = [], {}
    for _, r in cands.iterrows():
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
        out.append({"size": n, "legs": chosen, **evs})
    return out
