"""Backfill per-player Statcast expected stats for an MLB season.

Writes ``data/history/backfill/mlb/<season>/statcast_xstats.json.gz`` in the
format ``project547.history.statcast_xstats`` reads: {meta, batting, pitching}
keyed by MLBAM player id, each with pa/bip/ba/xba/slg/xslg/woba/xwoba. Source is
Baseball Savant (free) via the pybaseball expected-stats endpoints, the same feed
that produced the committed 2022-2025 files. Run in-season to keep the current
year's wOBA fresh for the lineup-level run model (roadmap T2.2b).

Run:  python scripts/build_statcast_xstats.py [season ...]
"""
from __future__ import annotations

import gzip
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from project547.clients import statcast  # noqa: E402
from project547.history import HISTORY_DIR  # noqa: E402


def _name(raw) -> str:
    """'Duran, Jarren' -> 'Jarren Duran' (Savant gives 'last, first')."""
    s = str(raw or "").strip()
    if "," in s:
        last, first = (p.strip() for p in s.split(",", 1))
        return f"{first} {last}".strip()
    return s


def _rows(df) -> dict:
    out: dict = {}
    if df is None or df.empty:
        return out
    for _, r in df.iterrows():
        pid = r.get("player_id")
        if pid is None:
            continue
        out[str(int(pid))] = {
            "name": _name(r.get("last_name, first_name")),
            "pa": int(r.get("pa") or 0),
            "bip": int(r.get("bip") or 0),
            "ba": _f(r.get("ba")), "xba": _f(r.get("est_ba")),
            "slg": _f(r.get("slg")), "xslg": _f(r.get("est_slg")),
            "woba": _f(r.get("woba")), "xwoba": _f(r.get("est_woba")),
        }
    return out


def _f(v):
    try:
        return round(float(v), 4)
    except (TypeError, ValueError):
        return None


def build(season: int) -> Path:
    batting = _rows(statcast.statcast_batter_expected(season))
    pitching = _rows(statcast.statcast_pitcher_expected(season))
    payload = {
        "meta": {"season": season,
                 "fetched_at": datetime.now(timezone.utc).isoformat(),
                 "n_batters": len(batting), "n_pitchers": len(pitching)},
        "batting": batting, "pitching": pitching,
    }
    out = HISTORY_DIR / "backfill" / "mlb" / str(season) / "statcast_xstats.json.gz"
    out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out, "wt") as f:
        json.dump(payload, f)
    print(f"{season}: wrote {len(batting)} batters, {len(pitching)} pitchers -> {out}")
    return out


if __name__ == "__main__":
    seasons = [int(x) for x in sys.argv[1:]] or [datetime.now().year]
    for s in seasons:
        build(s)
