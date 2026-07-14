"""Optimization & +EV math engine (DraftKings salary cap + Pick'em).

Three engine functions:

* ``calculate_pickem_edge``       -- CDF probability a stat clears a line.
* ``optimize_salary_cap_dfs``     -- PuLP LP maximizing projected points.
* ``generate_optimal_pickem_slips`` -- ranked highest-probability plays.

The CDF math uses the standard library ``statistics.NormalDist`` (no numpy /
scipy), keeping the engine flat and dependency-light.
"""

from __future__ import annotations

from statistics import NormalDist

from . import db_manager, edge

# Break-even-to-profitable Pick'em win rate. A 2-pick power play pays ~3x, and
# most flat books need ~54% just to overcome the hold, so 0.543 is the viability
# floor used throughout.
BREAK_EVEN = 0.543


# --------------------------------------------------------------------------- #
# 1. Pick'em edge
# --------------------------------------------------------------------------- #
def calculate_pickem_edge(
    proj_mean: float, proj_std: float, line_value: float
) -> dict:
    """Probability the stat lands OVER / UNDER ``line_value``.

    Models the stat as Normal(mean, std) and reads the CDF at the line.
    ``is_viable`` is True when the better side clears the 54.3% break-even.
    """
    std = float(proj_std) if proj_std and proj_std > 0 else 0.0
    if std <= 0:
        # Degenerate distribution: the projection is either fully over or under.
        over = 1.0 if proj_mean > line_value else 0.0
        under = 1.0 - over
    else:
        dist = NormalDist(float(proj_mean), std)
        under = dist.cdf(float(line_value))  # P(X <= line)
        over = 1.0 - under

    return {
        "over_win_rate": round(over, 4),
        "under_win_rate": round(under, 4),
        "is_viable": max(over, under) > BREAK_EVEN,
    }


# --------------------------------------------------------------------------- #
# 2. DraftKings salary-cap DFS optimizer
# --------------------------------------------------------------------------- #
# DraftKings classic roster templates. ``slots`` are exact position -> count
# requirements; edit here to add a sport or tune a structure.
ROSTER_RULES: dict[str, dict] = {
    "MLB": {"size": 10, "slots": {"P": 2, "C": 1, "1B": 1, "2B": 1,
                                  "3B": 1, "SS": 1, "OF": 3}},
    "WNBA": {"size": 6, "slots": {"G": 3, "F": 3}},
}


def optimize_salary_cap_dfs(sport: str, budget: int = 50000, conn=None) -> list:
    """Maximize total projected points under the DK salary cap via LP.

    Reads ``player_projections`` for ``sport`` and solves an integer program
    (PuLP / CBC) honoring the sport's roster slots and ``budget``. Returns the
    chosen players as dicts; empty list if the slate is infeasible.
    """
    import pulp  # lazy: Pick'em mode does not need the LP solver

    rules = ROSTER_RULES.get(sport.upper())
    if rules is None:
        raise ValueError(
            f"no roster template for sport {sport!r}; "
            f"known: {sorted(ROSTER_RULES)}"
        )

    own_conn = conn is None
    if own_conn:
        conn = db_manager.connect()
    try:
        players = db_manager.get_projections(conn, sport.upper())
    finally:
        if own_conn:
            conn.close()

    pool = [p for p in players if p.get("salary_dk") and p.get("position")]
    if not pool:
        return []

    prob = pulp.LpProblem("dfs_salary_cap", pulp.LpMaximize)
    x = {
        p["master_player_id"]: pulp.LpVariable(f"x_{i}", cat="Binary")
        for i, p in enumerate(pool)
    }

    # Objective: total projected points.
    prob += pulp.lpSum(
        p["projected_points"] * x[p["master_player_id"]] for p in pool
    )

    # Salary cap.
    prob += (
        pulp.lpSum(p["salary_dk"] * x[p["master_player_id"]] for p in pool)
        <= budget
    )
    # Exact roster size.
    prob += pulp.lpSum(x.values()) == rules["size"]
    # Per-position counts (position matched exactly).
    for pos, need in rules["slots"].items():
        prob += (
            pulp.lpSum(
                x[p["master_player_id"]] for p in pool if p["position"] == pos
            )
            == need
        )

    prob.solve(pulp.PULP_CBC_CMD(msg=False))
    if pulp.LpStatus[prob.status] != "Optimal":
        return []

    chosen = [p for p in pool if x[p["master_player_id"]].value() == 1]
    chosen.sort(key=lambda p: p["salary_dk"], reverse=True)
    return chosen


# --------------------------------------------------------------------------- #
# 3. Pick'em slip generator
# --------------------------------------------------------------------------- #
def generate_optimal_pickem_slips(
    sport: str, platform: str = "PrizePicks", conn=None, limit: int = 6
) -> list:
    """Rank the highest-probability over/under plays for a Pick'em book.

    Joins ``player_projections`` with ``market_lines`` where
    ``bookmaker == platform``, scores each with :func:`calculate_pickem_edge`,
    and returns the top ``limit`` viable plays sorted by distance from
    break-even (default 6 for a slip; pass a larger limit for a full board).
    Each play also carries BettingPros' second opinion (projection / EV /
    recommended side / opening price / public %) when present.
    """
    import json as _json
    own_conn = conn is None
    if own_conn:
        conn = db_manager.connect()
    try:
        lines = db_manager.get_market_lines(conn, sport.upper(), platform)
    finally:
        if own_conn:
            conn.close()

    plays = []
    for ln in lines:
        mean, std, line = ln["eff_mean"], ln["eff_std"], ln["line_value"]
        if mean is None or line is None:
            continue
        extra = {}
        if ln.get("extra_json"):
            try:
                extra = _json.loads(ln["extra_json"]) or {}
            except (ValueError, TypeError):
                extra = {}

        # Our per-stat model probability at the DFS line.
        model_over = edge.model_over_prob(mean, std, line)
        # BettingPros' probability, oriented to P(over).
        bp_prob = extra.get("bp_probability")
        bp_rec = (extra.get("bp_recommended") or "").lower()
        bp_over = (bp_prob if bp_rec == "over"
                   else (1 - bp_prob) if (bp_prob is not None and bp_rec == "under")
                   else None)
        # De-vigged sharp market and recent form.
        market_over = edge.devig_two_way(extra.get("over_odds"), extra.get("under_odds"))
        form_over = extra.get("form_l10")

        s = edge.score(
            model_over=model_over, bp_over=bp_over, form_over=form_over,
            market_over=market_over, dfs_line=line,
            consensus_line=extra.get("bp_line"),
            break_even=BREAK_EVEN, bet_rating=extra.get("bet_rating"),
        )
        if s["win_rate"] <= BREAK_EVEN:
            continue

        model_win = round(model_over if s["side"] == "OVER" else 1 - model_over, 4)
        plays.append(
            {
                "player_name": ln["player_name"],
                "sport": ln["sport"],
                "stat_type": ln["stat_type"],
                "line_value": line,
                "side": s["side"],
                "win_rate": s["win_rate"],          # calibrated ensemble
                "model_win": model_win,             # our model alone
                "confidence": s["confidence"],      # 0-100 composite
                "edge_vs_breakeven": s["edge_vs_breakeven"],
                "edge_vs_market": s["edge_vs_market"],
                "line_edge": s["line_edge"],        # soft-line gap vs sharp
                "agreement": s["agreement"],
                "n_signals": s["n_signals"],
                "proj_mean": round(mean, 3) if mean is not None else None,
                "proj_std": round(std, 3) if std is not None else None,
                "consensus_line": extra.get("bp_line"),
                "over_odds": ln["over_odds"],
                "under_odds": ln["under_odds"],
                # BettingPros second opinion (premium fields; None when absent).
                "bp_ev": extra.get("bp_ev"),
                "bp_projection": extra.get("bp_projection"),
                "bp_recommended": extra.get("bp_recommended"),
                "bet_rating": extra.get("bet_rating"),
                "public_pct_over": extra.get("public_pct_over"),
                "form_l10": extra.get("form_l10"),
                "platform": platform,
            }
        )

    # Rank by our composite confidence (edge vs market + agreement + soft line).
    plays.sort(key=lambda p: p["confidence"], reverse=True)
    return plays[:limit]
