"""BettingPros as baseline + validator.

A thin façade over ``clients/bettingpros`` for the app: turn BP's game offers
into a de-vigged **baseline** win probability, and surface BP's own **prop
projections / EV / recommended side** as a second opinion. We're not a BP clone
— BP is the baseline/validator we measure ourselves against and add our own math
on top of.

Pure aggregation (``_consensus_two_way``, ``_find_event``, ``_clean_props``) is
separated from the live fetch so it unit-tests offline; the fetchers are
defensive (any error -> None/[]) so a BP outage never breaks a page.
"""

from __future__ import annotations

import re
import statistics

from . import baseline


def _tok(name: str) -> set[str]:
    """Distinctive lowercased word tokens (len>=4) for fuzzy team matching."""
    return {w for w in re.split(r"[^a-z]+", (name or "").lower()) if len(w) >= 4}


def _side_for(participant: str, home: str, away: str) -> str | None:
    pt = _tok(participant)
    if pt & _tok(home):
        return "home"
    if pt & _tok(away):
        return "away"
    return None


def _median_american(odds: list[float]) -> float:
    """Consensus via the median in probability space, back to American."""
    from .odds import implied_prob
    p = statistics.median(implied_prob(o) for o in odds)
    p = min(max(p, 1e-4), 0.9999)
    return -100 * p / (1 - p) if p >= 0.5 else 100 * (1 - p) / p


def _consensus_two_way(offer_rows: list[dict], event_id, home: str, away: str):
    """(home_odds, away_odds) consensus American prices for a moneyline, or None."""
    h, a = [], []
    for r in offer_rows:
        if str(r.get("event_id")) != str(event_id):
            continue
        o = r.get("odds")
        if o in (None, ""):
            continue
        side = _side_for(r.get("participant") or "", home, away)
        (h if side == "home" else a if side == "away" else []).append(float(o))
    if not h or not a:
        return None
    return _median_american(h), _median_american(a)


def _find_event(events: list[dict], home: str, away: str) -> dict | None:
    th, ta = _tok(home), _tok(away)
    for ev in events or []:
        blob = _tok(str(ev))
        if th & blob and ta & blob:
            return ev
    return None


def game_baseline(bp_sport: str | None, date: str, home: str, away: str):
    """BettingPros de-vigged moneyline -> Baseline(source="bettingpros"), or None."""
    if not bp_sport:
        return None
    try:
        from .clients import bettingpros as _bp
        ev = _find_event(_bp.events(bp_sport, date), home, away)
        if not ev:
            return None
        ml = _bp.game_market_ids(bp_sport).get("moneyline")
        if not ml:
            return None
        rows = _bp.flatten_offers(_bp.offers(bp_sport, ml, [ev.get("id")]))
        cons = _consensus_two_way(rows, ev.get("id"), home, away)
        if not cons:
            return None
        return baseline.market_baseline(cons[0], cons[1], source="bettingpros")
    except Exception:
        return None


_PROP_FIELDS = ("participant", "player_team", "market_id", "bp_line",
                "bp_projection", "bp_ev", "bp_probability", "bp_recommended_side",
                "bp_bet_rating", "pick_pct_over", "perf_l10", "opp_rank")


def _clean_props(flat_rows: list[dict], teams: list[str] | None = None,
                 limit: int = 15) -> list[dict]:
    """Filter BP props to a matchup (by player team) and keep the premium fields,
    best EV first. ``teams`` None keeps the whole board."""
    toks = set().union(*[_tok(t) for t in teams]) if teams else None
    out = []
    for r in flat_rows:
        if r.get("bp_projection") is None and r.get("bp_ev") is None:
            continue
        if toks and not (_tok(r.get("player_team") or "") & toks):
            continue
        out.append({k: r.get(k) for k in _PROP_FIELDS})
    out.sort(key=lambda r: (r.get("bp_ev") if r.get("bp_ev") is not None else -1e9),
             reverse=True)
    return out[:limit]


def prop_projections(bp_sport: str | None, date: str, teams: list[str] | None = None,
                     limit: int = 15) -> list[dict]:
    """BettingPros prop projections (EV, recommended side, rating) for a matchup."""
    if not bp_sport:
        return []
    try:
        from .clients import bettingpros as _bp
        return _clean_props(_bp.flatten_props(_bp.props(bp_sport, date)), teams, limit)
    except Exception:
        return []
