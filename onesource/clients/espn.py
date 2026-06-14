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


_TTL_LIVE = 45  # seconds — live scores refresh fast but cap the API calls


def _parse_scoreboard(data: dict, sport_key: str) -> list[dict]:
    out = []
    for ev in data.get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        status = comp.get("status") or ev.get("status") or {}
        stype = status.get("type") or {}
        home = away = None
        for c in comp.get("competitors", []):
            t = c.get("team") or {}
            entry = {
                "team": t.get("displayName"), "abbrev": t.get("abbreviation"),
                "logo": t.get("logo"), "score": _to_num(c.get("score")),
                "record": (c.get("records") or [{}])[0].get("summary"),
            }
            (home, away) = (entry, away) if c.get("homeAway") == "home" else (home, entry)
        if not home or not away:
            continue
        out.append({
            "sport": sport_key, "game_id": ev.get("id"),
            "game_time": ev.get("date"),
            "state": stype.get("state"),          # pre / in / post
            "detail": stype.get("shortDetail"),   # "Q3 4:21", "Final", "8:00 PM"
            "home": home, "away": away,
        })
    return out


def _get_path(path: str, params: dict, endpoint: str = "scoreboard") -> dict:
    merged = {"limit": 1000, **params}
    resp = requests.get(f"{BASE}/{path}/{endpoint}", params=merged, timeout=30)
    resp.raise_for_status()
    return resp.json()


def scoreboard(sport_key: str, date: str) -> list[dict]:
    """All games on a date with live status + scores (for the scoreboard)."""
    compact = date.replace("-", "")
    data = cached_json(f"espn:scoreboard:{sport_key}:{date}", _TTL_LIVE,
                       lambda: _get(sport_key, {"dates": compact}))
    return _parse_scoreboard(data, sport_key)


def scoreboard_at(path: str, date: str, label: str) -> list[dict]:
    """Scoreboard for any ESPN league by raw path (e.g. 'soccer/eng.1'),
    for leagues we show scores for but don't project."""
    compact = date.replace("-", "")
    data = cached_json(f"espn:sbpath:{path}:{date}", _TTL_LIVE,
                       lambda: _get_path(path, {"dates": compact}))
    return _parse_scoreboard(data, label)


def _parse_box(data: dict) -> dict:
    box = data.get("boxscore", {})
    teams = []
    for t in box.get("players", []):
        meta = t.get("team", {})
        label = meta.get("abbreviation") or meta.get("displayName") or ""
        columns, rows = None, []
        for block in t.get("statistics", []):
            columns = block.get("labels") or block.get("names") or block.get("keys") or []
            for ath in block.get("athletes", []):
                stats = ath.get("stats", [])
                name = (ath.get("athlete", {}) or {}).get("displayName")
                if name and stats:
                    rows.append([name, *stats])
        teams.append({"team": label, "columns": ["Player", *(columns or [])],
                      "rows": rows})
    return {"teams": teams}


def box_score(sport_key: str, event_id) -> dict:
    """Generic per-team player stat tables for a game (works across ESPN
    sports), as {teams: [{team, columns, rows}], ...}. {} on any issue."""
    try:
        return _parse_box(_summary(sport_key, event_id))
    except Exception:
        return {}


def box_score_at(path: str, event_id) -> dict:
    """Box score for any ESPN league by raw path."""
    try:
        return _parse_box(_get_path(path, {"event": event_id}, endpoint="summary"))
    except Exception:
        return {}


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


# ESPN football boxscore: category name -> {our field: machine stat key}.
# Combo keys ("a/b", "a-b") are split — (key, index) takes that part.
_FBALL_CATS = {
    "passing": {
        "passing_yards": "passingYards",
        "passing_tds": "passingTouchdowns",
        "interceptions": "interceptions",
        "completions": ("completions/passingAttempts", 0),
        "pass_attempts": ("completions/passingAttempts", 1),
    },
    "rushing": {
        "rushing_yards": "rushingYards",
        "rushing_tds": "rushingTouchdowns",
        "rushing_attempts": "rushingAttempts",
        "long_rush": "longRushing",
    },
    "receiving": {
        "receptions": "receptions",
        "receiving_yards": "receivingYards",
        "receiving_tds": "receivingTouchdowns",
        "long_reception": "longReception",
        "targets": "receivingTargets",
    },
    "kicking": {
        "field_goals_made": ("fieldGoalsMade/fieldGoalAttempts", 0),
        "kicking_points": "totalKickingPoints",
    },
}


def _combo_part(raw, idx: int):
    for sep in ("/", "-"):
        if raw is not None and sep in str(raw):
            parts = str(raw).split(sep)
            return parts[idx] if idx < len(parts) else None
    return raw if idx == 0 else None


def _football_box(data: dict, event_id) -> list[dict]:
    """Per-player passing/rushing/receiving/kicking lines for a finished
    football game. Defensive: shaped to ESPN's documented football summary
    keys; verify against live data once the season is on."""
    box = data.get("boxscore", {})
    teams = box.get("players", [])
    abbr = [(t.get("team", {}) or {}).get("abbreviation")
            or (t.get("team", {}) or {}).get("displayName") or ""
            for t in teams]
    players: dict[str, dict] = {}
    for idx, t in enumerate(teams):
        opp = abbr[1 - idx] if len(abbr) == 2 else ""
        for block in t.get("statistics", []):
            fields = _FBALL_CATS.get((block.get("name") or "").lower())
            if not fields:
                continue
            keys = block.get("keys") or block.get("names") or []
            for ath in block.get("athletes", []):
                vals = dict(zip(keys, ath.get("stats", []) or []))
                name = (ath.get("athlete", {}) or {}).get("displayName")
                if not name or not vals:
                    continue
                row = players.setdefault(
                    name, {"game_pk": event_id, "opponent": opp, "name": name})
                for field, key in fields.items():
                    if isinstance(key, tuple):
                        v = _to_num(_combo_part(vals.get(key[0]), key[1]))
                    else:
                        v = _to_num(vals.get(key))
                    if v is not None:
                        row[field] = v
    for row in players.values():
        ry, recy = row.get("rushing_yards"), row.get("receiving_yards")
        if ry is not None or recy is not None:
            row["scrim_yards"] = (ry or 0) + (recy or 0)
        rtd, rectd = row.get("rushing_tds"), row.get("receiving_tds")
        if rtd is not None or rectd is not None:
            row["scrim_tds"] = (rtd or 0) + (rectd or 0)
    return list(players.values())


def box_player_logs(sport_key: str, event_id) -> list[dict]:
    """Per-player box-score lines for a finished game. Basketball: points,
    rebounds, assists, steals, blocks, threes. Football: passing/rushing/
    receiving/kicking stat lines. Returns [] on any issue."""
    try:
        data = _summary(sport_key, event_id)
    except Exception:
        return []
    if "football" in (SPORTS[sport_key].espn_path or ""):
        try:
            return _football_box(data, event_id)
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
