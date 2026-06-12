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


def _summary(sport_key: str, event_id) -> dict:
    sp = SPORTS[sport_key]
    resp = requests.get(f"{BASE}/{sp.espn_path}/summary",
                        params={"event": event_id}, timeout=30)
    resp.raise_for_status()
    return resp.json()


# ESPN box-score stat key -> our log field (basketball)
_BBALL_KEYS = {
    "points": "points", "rebounds": "rebounds", "assists": "assists",
    "steals": "steals", "blocks": "blocks",
}


def box_player_logs(sport_key: str, event_id) -> list[dict]:
    """Per-player box-score lines for a finished basketball game (points,
    rebounds, assists, steals, blocks, threes). Returns [] on any issue."""
    try:
        data = _summary(sport_key, event_id)
    except Exception:
        return []
    box = data.get("boxscore", {})
    teams = box.get("players", [])
    abbr = []
    for t in teams:
        team = t.get("team", {})
        abbr.append(team.get("abbreviation") or team.get("displayName") or "")
    rows = []
    for idx, t in enumerate(teams):
        opp = abbr[1 - idx] if len(abbr) == 2 else ""
        for block in t.get("statistics", []):
            keys = block.get("keys", []) or block.get("names", [])
            for ath in block.get("athletes", []):
                stats = ath.get("stats", [])
                if not stats:
                    continue
                vals = dict(zip(keys, stats))
                row = {"game_pk": event_id, "opponent": opp,
                       "name": ath.get("athlete", {}).get("displayName")}
                for k, field in _BBALL_KEYS.items():
                    row[field] = _to_num(vals.get(k))
                three = vals.get("threePointFieldGoalsMade-threePointFieldGoalsAttempted")
                if three and "-" in str(three):
                    row["three_made"] = _to_num(str(three).split("-")[0])
                pts, reb, ast = row.get("points"), row.get("rebounds"), row.get("assists")
                if None not in (pts, reb, ast):
                    row["pra"] = pts + reb + ast
                if row["name"]:
                    rows.append(row)
    return rows


def _to_num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def results_range(sport_key: str, start: str, end: str) -> list[dict]:
    """Completed games with final scores in [start, end]. ESPN rejects very
    long date ranges (observed 400s past ~1 year), so wide windows are
    chunked into <=150-day requests and merged."""
    from datetime import date, timedelta

    d0 = date.fromisoformat(start)
    d1 = date.fromisoformat(end)
    out: list[dict] = []
    seen: set = set()
    while d0 <= d1:
        chunk_end = min(d0 + timedelta(days=149), d1)
        rng = f"{d0.strftime('%Y%m%d')}-{chunk_end.strftime('%Y%m%d')}"
        data = cached_json(
            f"espn:results:{sport_key}:{rng}",
            _TTL_RESULTS,
            lambda rng=rng: _get(sport_key, {"dates": rng}),
        )
        for g in _parse_events(data):
            if (g["completed"] and g["home_score"] is not None
                    and g["game_id"] not in seen):
                seen.add(g["game_id"])
                out.append(g)
        d0 = chunk_end + timedelta(days=1)
    return out
