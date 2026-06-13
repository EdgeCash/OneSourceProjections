"""Multi-source expert consensus — where independent reads agree.

For each prop we have up to three *independent* opinions:

  - **Our model** — ``model_over_prob`` → an Over/Under lean and an EV.
  - **BettingPros experts** — ``bp_recommended_side`` plus ``bp_bet_rating``
    (their 0–5 analyst-confidence "star" rating) and ``bp_projection``. These
    are BettingPros' premium expert fields (populated when the BP_USER auth
    triple is configured).
  - **The public** — ``pick_pct_over`` from BettingPros' pick distribution.

When several of these lean the same way it's a stronger signal than any one
alone — the classic "consensus" play. ``consensus_table`` walks a published
slate and returns one row per prop with each source's lean, an agreement
score, and the supporting numbers, searchable by player/team/market. Pure over
the slate dict (``latest.json``), so it unit-tests offline.
"""

from __future__ import annotations

import math


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not (isinstance(v, float) and math.isnan(v))


def _norm_side(v) -> str | None:
    """Normalize a side label to 'Over'/'Under' (or None)."""
    if not isinstance(v, str):
        return None
    s = v.strip().lower()
    if s.startswith("o"):
        return "Over"
    if s.startswith("u"):
        return "Under"
    return None


def _model_side(p: dict) -> str | None:
    prob = p.get("model_over_prob")
    if not _is_num(prob):
        return None
    if prob >= 0.53:
        return "Over"
    if prob <= 0.47:
        return "Under"
    return None  # too close to call


def _public_side(p: dict, edge: float = 0.08) -> str | None:
    pct = p.get("pick_pct_over")
    if not _is_num(pct):
        return None
    if pct >= 0.5 + edge:
        return "Over"
    if pct <= 0.5 - edge:
        return "Under"
    return None


def prop_consensus(p: dict) -> dict | None:
    """Build a consensus row for one prop, or None if no source has a lean.

    Returns the per-source sides, the majority ``consensus`` side, ``n_sources``
    (how many independent reads have a lean), ``agree`` (how many back the
    consensus), ``unanimous`` (all sources agree and there are ≥2), plus the
    supporting numbers carried through for display/sorting.
    """
    model = _model_side(p)
    bp = _norm_side(p.get("bp_recommended_side"))
    public = _public_side(p)
    sides = [s for s in (model, bp, public) if s]
    if not sides:
        return None
    overs = sides.count("Over")
    unders = sides.count("Under")
    consensus = "Over" if overs > unders else "Under" if unders > overs else None
    agree = max(overs, unders)
    n = len(sides)
    return {
        "player": p.get("player"), "team": p.get("team"),
        "market": p.get("market"),
        "line": p.get("line") if _is_num(p.get("line")) else p.get("bp_line"),
        "model_side": model, "bp_side": bp, "public_side": public,
        "consensus": consensus, "n_sources": n, "agree": agree,
        "unanimous": n >= 2 and agree == n,
        "bp_rating": p.get("bp_bet_rating") if _is_num(p.get("bp_bet_rating")) else None,
        "bp_ev": p.get("bp_ev") if _is_num(p.get("bp_ev")) else None,
        "model_prob": p.get("model_over_prob") if _is_num(p.get("model_over_prob")) else None,
        "ev": p.get("ev") if _is_num(p.get("ev")) else None,
        "pick_pct_over": p.get("pick_pct_over") if _is_num(p.get("pick_pct_over")) else None,
    }


def _matches(row: dict, q: str) -> bool:
    if not q:
        return True
    q = q.lower()
    return any(q in str(row.get(f, "")).lower()
               for f in ("player", "team", "market"))


def consensus_table(slate: dict, query: str = "", min_sources: int = 1,
                    multi_only: bool = False) -> list[dict]:
    """Consensus rows across every sport's props in a published slate.

    ``slate`` is ``{sport: {"props": [...], ...}}`` (one date of latest.json).
    Filters by ``query`` (player/team/market substring) and ``min_sources``;
    set ``multi_only`` to keep only props where ≥2 sources have a lean. Sorted
    by sources, then agreement, then BettingPros rating — strongest consensus
    first."""
    rows = []
    for sport, bundle in (slate or {}).items():
        for p in bundle.get("props") or []:
            row = prop_consensus(p)
            if row is None:
                continue
            row["sport"] = sport
            if row["n_sources"] < min_sources:
                continue
            if multi_only and row["n_sources"] < 2:
                continue
            if not _matches(row, query):
                continue
            rows.append(row)
    rows.sort(key=lambda r: (r["n_sources"], r["agree"], r["bp_rating"] or 0,
                             abs(r["ev"] or 0)), reverse=True)
    return rows
