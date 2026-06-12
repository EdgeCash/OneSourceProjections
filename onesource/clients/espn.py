"""ESPN public scoreboard API — free, no key. Slate + final scores for
WNBA, NBA, NFL, NCAAF, and NHL (MLB uses statsapi.mlb.com instead).

Endpoint: site.api.espn.com/apis/site/v2/sports/{path}/scoreboard
Accepts dates=YYYYMMDD or a YYYYMMDD-YYYYMMDD range.
"""

from __future__ import annotations

import requests

from ..cache import cached_json
from ..sports import SPORTS

BASE = "https://site.api.espn.com/apis/site/v2/sports"
_TTL_SLATE = 15 * 60
_TTL_RESULTS = 6 * 60 * 60


def _get(sport_key: str, params: dict) -> dict:
    sp = SPORTS[sport_key]
    merged = {"limit": 1000, **sp.espn_params, **params}
    resp = requests.get(f"{BASE}/{sp.espn_path}/scoreboard", params=merged, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _parse_events(data: dict) -> list[dict]:
    out = []
    for ev in data.get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        home = away = None
        for c in comp.get("competitors", []):
            entry = {
                "team": (c.get("team") or {}).get("displayName"),
                "abbrev": (c.get("team") or {}).get("abbreviation"),
                "score": float(c["score"]) if c.get("score") not in (None, "") else None,
            }
            if c.get("homeAway") == "home":
                home = entry
            else:
                away = entry
        if not home or not away:
            continue
        out.append(
            {
                "game_id": ev.get("id"),
                "date": (ev.get("date") or "")[:10],
                "game_time": ev.get("date"),
                "completed": (ev.get("status") or {}).get("type", {}).get("completed", False),
                "home_team": home["team"],
                "away_team": away["team"],
                "home_score": home["score"],
                "away_score": away["score"],
            }
        )
    return out


def slate(sport_key: str, date: str) -> list[dict]:
    """Games scheduled on a date (YYYY-MM-DD)."""
    compact = date.replace("-", "")
    data = cached_json(
        f"espn:slate:{sport_key}:{date}",
        _TTL_SLATE,
        lambda: _get(sport_key, {"dates": compact}),
    )
    return [g for g in _parse_events(data) if not g["completed"]]


def results_range(sport_key: str, start: str, end: str) -> list[dict]:
    """Completed games with final scores in [start, end]."""
    rng = f"{start.replace('-', '')}-{end.replace('-', '')}"
    data = cached_json(
        f"espn:results:{sport_key}:{rng}",
        _TTL_RESULTS,
        lambda: _get(sport_key, {"dates": rng}),
    )
    return [
        g for g in _parse_events(data)
        if g["completed"] and g["home_score"] is not None
    ]
