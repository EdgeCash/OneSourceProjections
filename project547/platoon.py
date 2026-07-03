"""Batter platoon (vs LHP / vs RHP) adjustment for props.

Two committed pieces finally wired together:
  * handedness — ``data/history/mlb_handedness.json`` (bats/throws, harvested from
    the retrosheet biofile via scripts/import_mlb_handedness.py);
  * platoon splits — ``data/history/backfill/mlb/<season>/splits.json.gz``
    (per-hitter vL/vR avg/slg/PA, keyed by MLBAM id).

Given the opposing starter's throwing hand, a hitter's projection is nudged
toward their split vs that hand. The signal is **shrunk by the split's sample
size** (a 40-PA platoon line barely moves the number) so it can only help a
little and can't run away on noise. Current-season splits lag, so the prior
season is used as the prior — platoon tendency is stable year to year.
"""
from __future__ import annotations

import gzip
import json
from functools import lru_cache

from .config import REPO_ROOT
from .names import normalize

_HAND_PATH = REPO_ROOT / "data" / "history" / "mlb_handedness.json"
PLATOON_PRIOR_PA = 120.0   # PA of "league-average platoon" prior (shrink strength)


@lru_cache(maxsize=1)
def _handedness() -> dict:
    try:
        return json.loads(_HAND_PATH.read_text())
    except Exception:
        return {}


# Live, id-keyed handedness for the current slate, resolved from the StatsAPI
# people endpoint (see prime_hands). Preferred over the committed name-keyed
# retrosheet map: a player id can't collide the way normalized names can, and the
# people endpoint is authoritative for recent call-ups the harvested map lags.
_LIVE_BY_ID: dict[int, dict] = {}


def throws(name: str, player_id=None) -> str | None:
    """'L' / 'R' (pitcher throwing hand), or None if unknown. Prefers the live
    id-keyed map (primed from the people endpoint) and falls back to the
    committed retrosheet name map."""
    if player_id is not None:
        rec = _LIVE_BY_ID.get(int(player_id))
        if rec and rec.get("throws"):
            return rec["throws"]
    rec = _handedness().get(normalize(name or ""))
    return (rec or {}).get("throws")


def bats(name: str, player_id=None) -> str | None:
    """'L' / 'R' / 'B' (switch), or None if unknown. Live id-keyed first, then
    the committed name map."""
    if player_id is not None:
        rec = _LIVE_BY_ID.get(int(player_id))
        if rec and rec.get("bats"):
            return rec["bats"]
    rec = _handedness().get(normalize(name or ""))
    return (rec or {}).get("bats")


def _persist(mapping: dict) -> int:
    """Merge a {norm_name: {bats, throws}} mapping into the committed handedness
    file, returning the number of newly changed names. Clears the read cache so
    the name-keyed lookups see the additions."""
    if not mapping:
        return 0
    data = dict(_handedness())
    added = 0
    for nm, entry in mapping.items():
        if nm and entry != data.get(nm):
            data[nm] = entry
            added += 1
    if added:
        _HAND_PATH.write_text(json.dumps(data, separators=(",", ":"),
                                         sort_keys=True))
        _handedness.cache_clear()
    return added


def prime_hands(pairs) -> int:
    """Resolve live bats/throws **by player id** for a slate from the StatsAPI
    people endpoint, so the day's handedness doesn't depend on the stale,
    name-keyed retrosheet map. ``pairs`` is an iterable of ``(player_id, name)``.
    Populates the in-memory id-keyed map (used first by throws()/bats()) and
    backfills the committed name map for offline fallback. Returns the number of
    players fetched. Best-effort: statsapi being unreachable resolves nothing."""
    from .clients import mlb_statsapi

    ids = []
    for pid, _name in pairs:
        if pid and int(pid) not in _LIVE_BY_ID:
            ids.append(int(pid))
    if not ids:
        return 0
    try:
        fetched = mlb_statsapi.people_handedness(ids)
    except Exception:
        return 0
    persist: dict = {}
    for pid, rec in fetched.items():
        entry = {"bats": rec.get("bats"), "throws": rec.get("throws")}
        _LIVE_BY_ID[int(pid)] = entry
        nm = normalize(rec.get("name") or "")
        if nm and (rec.get("bats") or rec.get("throws")):
            persist[nm] = entry
    _persist(persist)
    return len(fetched)


def refresh_handedness(pairs) -> int:
    """Fill handedness for players the committed retrosheet map misses (recent
    debuts default to R/R until the live feed fills them in — see
    scripts/import_mlb_handedness.py). ``pairs`` is an iterable of
    ``(player_id, name)`` from the live slate; any whose normalized name is
    absent (or has a null bats/throws) is fetched in one batched call to the
    people endpoint and merged into ``mlb_handedness.json``. Returns the number
    of newly resolved players. Best-effort: statsapi being unreachable is not an
    error, it just resolves nothing."""
    from .clients import mlb_statsapi

    current = _handedness()
    missing_ids = []
    for pid, name in pairs:
        if not pid:
            continue
        rec = current.get(normalize(name or ""))
        if rec and rec.get("bats") and rec.get("throws"):
            continue
        missing_ids.append(int(pid))
    if not missing_ids:
        return 0

    try:
        fetched = mlb_statsapi.people_handedness(missing_ids)
    except Exception:
        return 0

    persist: dict = {}
    for pid, rec in fetched.items():
        nm = normalize(rec.get("name") or "")
        if not (rec.get("bats") or rec.get("throws")):
            continue
        entry = {"bats": rec.get("bats"), "throws": rec.get("throws")}
        if pid:
            _LIVE_BY_ID[int(pid)] = entry
        if nm:
            persist[nm] = entry
    return _persist(persist)


@lru_cache(maxsize=6)
def _hitting_splits(season: int) -> dict:
    path = (REPO_ROOT / "data" / "history" / "backfill" / "mlb" / str(season)
            / "splits.json.gz")
    if not path.exists():
        return {}
    try:
        return json.load(gzip.open(path)).get("hitting", {})
    except Exception:
        return {}


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _live_split(player_id, key: str, season: int) -> dict | None:
    """Current-season vL/vR line straight from the statSplits endpoint (the
    freshest source), or None. Cached in-process per (id, season)."""
    cache = _live_split.__dict__.setdefault("_c", {})
    ck = (int(player_id), season)
    if ck not in cache:
        from .clients import mlb_statsapi
        try:
            cache[ck] = mlb_statsapi.hitter_platoon_splits_live(int(player_id), season)
        except Exception:
            cache[ck] = {}
    sp = (cache[ck] or {}).get(key)
    if sp and sp.get("plateAppearances"):
        return {"avg": _f(sp.get("avg")), "slg": _f(sp.get("slg")),
                "pa": _f(sp.get("plateAppearances")) or 0.0}
    return None


def hitter_split(player_id, vs_throws: str, season: int) -> dict | None:
    """The hitter's split line (avg/slg/pa) vs the given hand. Freshest source
    wins: the live current-season statSplits feed, then the committed backfill
    for this season, then the prior season as the stable prior. ``vs_throws`` is
    the pitcher's hand ('L'/'R')."""
    if player_id is None or vs_throws not in ("L", "R"):
        return None
    key = "vl" if vs_throws == "L" else "vr"
    live = _live_split(player_id, key, season)
    if live and (live.get("pa") or 0) > 0:
        return live
    for s in (season, season - 1):
        row = _hitting_splits(s).get(str(int(player_id))) if player_id else None
        if row and key in row:
            sp = row[key]
            return {"avg": _f(sp.get("avg")), "slg": _f(sp.get("slg")),
                    "pa": _f(sp.get("plateAppearances")) or 0.0}
    return None


def platoon_mult(player_id, vs_throws: str, season: int, overall: float,
                 stat: str = "slg") -> float:
    """Multiplier (~0.7–1.4) to apply to a hitter's overall ``stat`` rate given
    the opposing hand, shrunk toward 1.0 by the split's PA. 1.0 when unknown."""
    sp = hitter_split(player_id, vs_throws, season)
    if not sp or not overall or overall <= 0:
        return 1.0
    val = sp.get(stat)
    if val is None or val <= 0:
        return 1.0
    raw = val / overall
    pa = sp.get("pa") or 0.0
    w = pa / (pa + PLATOON_PRIOR_PA)          # sample-size shrinkage
    mult = 1.0 + (raw - 1.0) * w
    return float(min(max(mult, 0.65), 1.45))  # clamp against junk splits
