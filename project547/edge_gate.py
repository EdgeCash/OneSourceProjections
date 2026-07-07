"""Per-market demonstrated-edge gate — wager only where we've proven an edge.

The old curated process treated model EV as edge uniformly: anything clearing the
2-6% band got pushed, in every market and sport. But our edge is wildly uneven by
market — a 2-6% model-vs-market disagreement on an *efficient* market is noise,
not edge, and those are the plays that lost. This gate makes curation and staking
conditional on **demonstrated, market-specific edge**, measured by realized
closing-line value (CLV) — the leading indicator the whole thesis rests on
(positive CLV = our number beat the close = real edge, ahead of the noisy ROI).

For each (sport, market) it rolls the graded results ledger and classifies:

  CLEARED   — enough CLV sample and avg CLV >= floor -> full curation + full stake
  GATED     — enough (a higher bar) CLV sample and avg CLV clearly negative
              -> proven no edge -> suppressed (PASS, zero stake)
  PROBATION — not enough sample yet to judge -> bet small to gather CLV, capped
              below the top tier

Asymmetric on purpose: easy-ish to clear on positive CLV, hard to gate a market
off (needs more, clearly-negative evidence) so we don't kill a market on noise.
The window is rolling, so a market re-earns (or loses) its status as data accrues.
"""

from __future__ import annotations

from datetime import date, timedelta

from . import config, results

CLEARED = "cleared"
PROBATION = "probation"
GATED = "gated"

# Backtest-driven hard hold (docs/MODEL_REPAIR.md P1/P5): markets with a
# demonstrated negative walk-forward ROI at the close — even after calibration
# they lose ~15-20% (efficient markets we don't beat). Held to projections-only
# regardless of live CLV until they earn positive rolling out-of-sample ROI, so
# they never surface as a play or draw a stake at launch. Remove a pair here once
# fit_calibration/backtest shows it clears.
HELD_MARKETS = {
    ("NBA", "moneyline"),
    ("NFL", "moneyline"),
    ("NHL", "moneyline"),
}


def _in_window(row_date: str, asof: str, window_days: int) -> bool:
    if not window_days:
        return True
    try:
        d = date.fromisoformat(str(row_date)[:10])
        a = date.fromisoformat(str(asof)[:10])
    except (TypeError, ValueError):
        return True
    return (a - d).days <= window_days and d <= a


def market_stats(ledger: list[dict] | None = None, asof: str | None = None,
                 window_days: int | None = None) -> dict:
    """Rolling per-(sport, market) record from the graded ledger. Returns
    ``{(sport, market): {n, clv_n, avg_clv, clv_pos_rate, roi, win_rate}}``."""
    if ledger is None:
        ledger = results.load_ledger()
    window_days = config.GATE_WINDOW_DAYS if window_days is None else window_days
    if asof is None:
        dates = [str(r.get("date")) for r in ledger if r.get("date")]
        asof = max(dates) if dates else date.today().isoformat()

    acc: dict = {}
    for r in ledger:
        if "pnl" not in r or not _in_window(r.get("date"), asof, window_days):
            continue
        key = (r.get("sport"), r.get("market"))
        a = acc.setdefault(key, {"n": 0, "clv_n": 0, "clv_sum": 0.0,
                                 "clv_pos": 0, "pnl": 0.0, "wins": 0, "decided": 0})
        a["n"] += 1
        a["pnl"] += r.get("pnl", 0.0) or 0.0
        if r.get("won") is not None:
            a["decided"] += 1
            a["wins"] += 1 if r.get("won") else 0
        clv = r.get("clv")
        if clv is not None:
            a["clv_n"] += 1
            a["clv_sum"] += clv
            a["clv_pos"] += 1 if clv > 0 else 0

    out = {}
    for key, a in acc.items():
        out[key] = {
            "n": a["n"], "clv_n": a["clv_n"],
            "avg_clv": round(a["clv_sum"] / a["clv_n"], 4) if a["clv_n"] else None,
            "clv_pos_rate": round(a["clv_pos"] / a["clv_n"], 4) if a["clv_n"] else None,
            "roi": round(a["pnl"] / a["n"], 4) if a["n"] else None,
            "win_rate": round(a["wins"] / a["decided"], 4) if a["decided"] else None,
        }
    return out


def classify(stat: dict | None) -> str:
    """Gate verdict from a market's rolling stats (see module docstring)."""
    if not stat:
        return PROBATION
    clv_n = stat.get("clv_n", 0)
    avg = stat.get("avg_clv")
    if avg is None:
        return PROBATION
    if clv_n >= config.GATE_CLEAR_MIN and avg >= config.GATE_CLV_FLOOR:
        return CLEARED
    # gate off only on a bigger, clearly-negative sample (don't kill on noise)
    if clv_n >= config.GATE_OFF_MIN and avg <= config.GATE_OFF_CLV:
        return GATED
    return PROBATION


def gate_table(ledger: list[dict] | None = None, asof: str | None = None,
               window_days: int | None = None) -> dict:
    """``{(sport, market): {status, ...stats}}`` for every market with history."""
    stats = market_stats(ledger, asof, window_days)
    return {k: {"status": classify(v), **v} for k, v in stats.items()}


def status_for(sport: str, market: str, table: dict | None = None) -> str:
    """Gate status for one market. Unknown / no-history markets are PROBATION
    (bet small to gather CLV, never a top-tier play until proven)."""
    if (sport, market) in HELD_MARKETS:
        return GATED           # backtest-proven loser: projections-only, no stake
    if table is None:
        table = gate_table()
    entry = table.get((sport, market))
    return entry["status"] if entry else PROBATION


# ---------------------------------------------------------------------------
# Effects on the wagering flow
# ---------------------------------------------------------------------------

# Ordered play_tier "kind"s, best->worst, used to cap a tier by gate status.
_TIER_ORDER = ["verify", "core", "watch", "lean", "pass"]


def cap_tier(kind: str, status: str) -> str:
    """Cap a play_tier ``kind`` by the market's gate status.

    - GATED     -> "pass" (no demonstrated edge; never curated)
    - PROBATION -> at most "lean" (unproven; small, non-headline plays only)
    - CLEARED   -> unchanged
    ``verify`` (a too-large-edge warning) is preserved either way — it's a caution,
    not a recommendation.
    """
    if kind == "verify":
        return kind
    if status == GATED:
        return "pass"
    if status == PROBATION and kind == "core":
        return "lean"
    return kind


def stake_mult(status: str) -> float:
    """Kelly multiplier by gate status: full when cleared, reduced on probation
    (enough to gather CLV), zero when gated off."""
    return {CLEARED: 1.0, PROBATION: config.GATE_PROBATION_STAKE, GATED: 0.0}.get(
        status, config.GATE_PROBATION_STAKE)


def is_curatable(status: str) -> bool:
    """Whether a market may produce a pushed/curated play at all."""
    return status != GATED
