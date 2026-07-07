"""Per-batter wOBA for the lineup-level run model (roadmap T2.2b).

The lineup engine (models/game.lineup_offense_runs) turns a lineup's mean wOBA
into runs. This module builds that per-batter wOBA from the best source we hold:

- **Statcast** wOBA blended with **xwOBA** (quality-of-contact stabilizes faster),
  PA-shrunk toward league, keyed by player id — the THE-BAT-X-style input.
- a caller-supplied name-keyed proxy (AVG/SLG) as the fallback for batters
  missing from Statcast (call-ups, thin samples).

Optionally platoon-adjusts each batter vs the starting pitcher's hand, damped by
the share of the game the starter covers (the rest is a mixed-hand bullpen).
Pure lookups over cached data, so the same construction runs live and in the
walk-forward validation (scripts/validate_lineup_runs.py)."""
from __future__ import annotations

from functools import lru_cache

from . import history, platoon
from .models.game import LEAGUE_WOBA

BAT_PRIOR_PA = 150.0     # shrink thin Statcast samples toward league wOBA
XWOBA_WEIGHT = 0.5       # weight on expected wOBA vs actual (xwOBA stabilizes faster)


@lru_cache(maxsize=8)
def statcast_by_id(season: int) -> dict:
    """{player_id: {"woba": PA-shrunk wOBA, "slg": overall SLG}} for a season."""
    bat = (history.statcast_xstats(season) or {}).get("batting", {}) or {}
    out: dict = {}
    for pid, r in bat.items():
        woba, xwoba = r.get("woba"), r.get("xwoba")
        pa = r.get("pa") or 0
        vals = [v for v in (woba, xwoba) if v is not None]
        if not vals:
            continue
        base = (XWOBA_WEIGHT * xwoba + (1 - XWOBA_WEIGHT) * woba
                if (woba is not None and xwoba is not None) else vals[0])
        w = pa / (pa + BAT_PRIOR_PA)
        try:
            out[int(pid)] = {"woba": w * base + (1 - w) * LEAGUE_WOBA,
                             "slg": r.get("slg")}
        except (TypeError, ValueError):
            continue
    return out


def batter_woba(pid, season: int, name: str | None = None,
                proxy: dict | None = None):
    """One batter's wOBA: Statcast (by id) first, else the name-keyed proxy."""
    if pid is not None:
        try:
            rec = statcast_by_id(season).get(int(pid))
        except (TypeError, ValueError):
            rec = None
        if rec is not None:
            return rec["woba"]
    if proxy is not None and name is not None:
        return proxy.get(name)
    return None


def lineup_woba(batters, season: int, starter_throws: str | None = None,
                proxy: dict | None = None, starter_share: float = 0.0):
    """Mean (optionally platoon-adjusted) wOBA of a posted lineup.

    ``batters``: list of (player_id, norm_name). ``starter_throws`` 'L'/'R'
    enables the platoon nudge vs that hand, damped by ``starter_share`` (the
    fraction of the game the starter covers). Returns None if no batter resolves.
    """
    ws = []
    scast = statcast_by_id(season)
    for pid, nm in batters:
        w = batter_woba(pid, season, nm, proxy)
        if w is None:
            continue
        if starter_throws and pid is not None:
            try:
                slg = (scast.get(int(pid)) or {}).get("slg")
            except (TypeError, ValueError):
                slg = None
            if slg:
                mult = platoon.platoon_mult(pid, starter_throws, season, slg, stat="slg")
                if starter_share:  # batter only gets the edge for the starter's innings
                    mult = 1.0 + (mult - 1.0) * starter_share
                w = w * mult
        ws.append(w)
    return sum(ws) / len(ws) if ws else None
