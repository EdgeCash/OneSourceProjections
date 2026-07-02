"""Home-plate umpire assignment + scoring/strikeout tendency.

Two pieces wired together, mirroring the platoon module:
  * assignment — the live home-plate ump for a game, from the boxscore
    ``officials`` block (posted a few hours pre-game);
  * tendency — a committed per-ump table (``data/history/mlb_umpires.json``,
    built by scripts/build_mlb_umpires.py) of runs / strikeout / walk indexes
    vs the league average, already shrunk toward 1.0 by the ump's game count.

The indexes are surfaced as game context and feed a small, clamped, config-
gated nudge to the scoring/strikeout environment (see config.UMPIRE_*). The
table's indexes are pre-shrunk, so the model just clamps and weights them.
"""
from __future__ import annotations

import json
from functools import lru_cache

from . import config
from .clients import mlb_statsapi
from .config import REPO_ROOT
from .names import normalize

_TABLE_PATH = REPO_ROOT / "data" / "history" / "mlb_umpires.json"


@lru_cache(maxsize=1)
def _table() -> dict:
    try:
        raw = json.loads(_TABLE_PATH.read_text())
    except Exception:
        return {"umpires": {}, "league": {}}
    # index by normalized name for tolerant lookup
    raw["_by_norm"] = {normalize(k): v for k, v in raw.get("umpires", {}).items()}
    return raw


def tendency(name: str | None) -> dict | None:
    """The ump's committed tendency row (runs_idx / k_idx / bb_idx / games), or
    None if we've never seen them (rookies, or a table that isn't built yet)."""
    if not name:
        return None
    return _table().get("_by_norm", {}).get(normalize(name))


def _clamped(idx: float | None, weight: float, clamp: float) -> float:
    """Turn a pre-shrunk index (1.0 = neutral) into a weighted, clamped
    multiplier. weight scales how much of the index we trust; clamp caps it."""
    if not idx or weight <= 0:
        return 1.0
    adj = weight * (float(idx) - 1.0)
    if clamp:
        adj = max(-clamp, min(clamp, adj))
    return 1.0 + adj


def runs_factor(name: str | None) -> float:
    """Game-level run multiplier for the home-plate ump (1.0 = neutral)."""
    t = tendency(name)
    return _clamped((t or {}).get("runs_idx"),
                    config.UMPIRE_RUNS_WEIGHT, config.UMPIRE_RUNS_CLAMP)


def k_factor(name: str | None) -> float:
    """Strikeout-rate multiplier for the home-plate ump (1.0 = neutral)."""
    t = tendency(name)
    return _clamped((t or {}).get("k_idx"),
                    config.UMPIRE_K_WEIGHT, config.UMPIRE_K_CLAMP)


def assignment(game_pk: int) -> dict | None:
    """The live home-plate ump for a game {name, id}, or None if the crew
    isn't posted yet."""
    return mlb_statsapi.game_officials(game_pk).get("home_plate")


def for_game(game_pk: int) -> dict | None:
    """Combined surface for the pipeline: the assigned home-plate ump plus their
    tendency indexes and the run/K multipliers we apply. None until the crew is
    posted. Tendency fields are None for a never-seen ump but the assignment
    still surfaces."""
    hp = assignment(game_pk)
    if not hp:
        return None
    t = tendency(hp.get("name")) or {}
    return {
        "name": hp.get("name"),
        "id": hp.get("id"),
        "games": t.get("games"),
        "runs_idx": t.get("runs_idx"),
        "k_idx": t.get("k_idx"),
        "bb_idx": t.get("bb_idx"),
        "runs_factor": round(runs_factor(hp.get("name")), 4),
        "k_factor": round(k_factor(hp.get("name")), 4),
    }
