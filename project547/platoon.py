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


def throws(name: str) -> str | None:
    """'L' / 'R' (pitcher throwing hand), or None if unknown."""
    rec = _handedness().get(normalize(name or ""))
    return (rec or {}).get("throws")


def bats(name: str) -> str | None:
    """'L' / 'R' / 'B' (switch), or None if unknown."""
    rec = _handedness().get(normalize(name or ""))
    return (rec or {}).get("bats")


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

    added = 0
    data = dict(current)  # copy the cached map to mutate + persist
    for rec in fetched.values():
        nm = normalize(rec.get("name") or "")
        if not nm or not (rec.get("bats") or rec.get("throws")):
            continue
        prev = data.get(nm)
        entry = {"bats": rec.get("bats"), "throws": rec.get("throws")}
        if prev != entry:
            data[nm] = entry
            added += 1
    if added:
        _HAND_PATH.write_text(json.dumps(data, separators=(",", ":"),
                                         sort_keys=True))
        _handedness.cache_clear()  # so throws()/bats() see the new names
    return added


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


def hitter_split(player_id, vs_throws: str, season: int) -> dict | None:
    """The hitter's split line (avg/slg/pa) vs the given hand, from ``season``
    then the prior season. ``vs_throws`` is the pitcher's hand ('L'/'R')."""
    if player_id is None or vs_throws not in ("L", "R"):
        return None
    key = "vl" if vs_throws == "L" else "vr"
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
