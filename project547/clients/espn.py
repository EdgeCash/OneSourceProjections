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
                # ESPN season type: 1=preseason, 2=regular season,
                # 3=postseason, 4=offseason/all-star (verified against live
                # payloads). None when ESPN omits it. results_range drops
                # non-regular/post types; slate keeps everything so a
                # preseason slate still displays.
                "season_type": (ev.get("season") or {}).get("type"),
                # neutral-site venue (bowls, Super Bowl, internationals)
                "neutral": bool(comp.get("neutralSite") or False),
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
            # unfiltered here (a preseason scoreboard should still display) —
            # carried so callers can filter; semantics as in _parse_events
            "season_type": (ev.get("season") or {}).get("type"),
            "neutral": bool(comp.get("neutralSite") or False),
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
# Combo keys ("a/b", "a-b") are split — (key, index) takes that part. Field
# names match the committed backfill schema so the forward store and history
# line up (pass_yards/rush_yards/rec_yards/scrimmage_yards/carries/…).
_FBALL_CATS = {
    "passing": {
        "pass_yards": "passingYards",
        "pass_touchdowns": "passingTouchdowns",
        "interceptions": "interceptions",
        "pass_completions": ("completions/passingAttempts", 0),
        "pass_attempts": ("completions/passingAttempts", 1),
    },
    "rushing": {
        "rush_yards": "rushingYards",
        "rush_touchdowns": "rushingTouchdowns",
        "carries": "rushingAttempts",
        "long_rush": "longRushing",
    },
    "receiving": {
        "receptions": "receptions",
        "rec_yards": "receivingYards",
        "rec_touchdowns": "receivingTouchdowns",
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
        ry, recy = row.get("rush_yards"), row.get("rec_yards")
        if ry is not None or recy is not None:
            row["scrimmage_yards"] = (ry or 0) + (recy or 0)
        rtd, rectd = row.get("rush_touchdowns"), row.get("rec_touchdowns")
        if rtd is not None or rectd is not None:
            row["scrim_tds"] = (rtd or 0) + (rectd or 0)
    return list(players.values())


# ESPN NHL boxscore blocks ("forwards"/"defenses"/"skaters" and "goalies"):
# machine stat key -> our log field. Field names match the committed NHL
# backfill schema (scripts/import_nhl_skaters.py ->
# data/history/backfill/nhl/<year>/player_games.jsonl.gz) so the forward
# store lines up with history and with what models/nhl_props.py and
# playerlogs.MARKET_STAT expect (shots/goals/assists/points/blocks/hits/pim;
# saves/shots_against/goals_against). ESPN's ``shotsTotal`` is shots on goal
# (cross-checked vs the backfill on a live 2025-01-15 CAR@BUF payload) and
# carries no ``points`` key, so points = goals + assists like the importer.
_NHL_SKATER_KEYS = {
    "goals": "goals", "assists": "assists", "shotsTotal": "shots",
    "blockedShots": "blocks", "hits": "hits", "penaltyMinutes": "pim",
}
_NHL_GOALIE_KEYS = {
    "saves": "saves", "shotsAgainst": "shots_against",
    "goalsAgainst": "goals_against", "penaltyMinutes": "pim",
}
_NHL_BLOCKS = {
    "forwards": ("F", _NHL_SKATER_KEYS), "defenses": ("D", _NHL_SKATER_KEYS),
    "skaters": ("F", _NHL_SKATER_KEYS), "goalies": ("G", _NHL_GOALIE_KEYS),
}


def _toi_seconds(v):
    """'MM:SS' (or 'H:MM:SS') time-on-ice -> integer seconds, matching the
    backfill's ``toi`` unit; None on anything unparseable."""
    if v is None:
        return None
    s = str(v).strip()
    if ":" in s:
        try:
            sec = 0
            for p in s.split(":"):
                sec = sec * 60 + int(p)
            return sec
        except ValueError:
            return None
    n = _to_num(s)
    return int(n) if n is not None else None


def _hockey_box(data: dict, event_id) -> list[dict]:
    """Per-player skater + goalie lines for a finished NHL game, shaped like
    the committed NHL backfill rows (full team names, F/D/G positions)."""
    box = data.get("boxscore", {})
    teams = box.get("players", [])
    # full names to match the backfill's team/opponent convention
    names = [(t.get("team", {}) or {}).get("displayName")
             or (t.get("team", {}) or {}).get("abbreviation") or ""
             for t in teams]
    rows = []
    for idx, t in enumerate(teams):
        opp = names[1 - idx] if len(names) == 2 else ""
        for block in t.get("statistics", []):
            spec = _NHL_BLOCKS.get((block.get("name") or "").lower())
            if not spec:
                continue
            default_pos, fields = spec
            keys = block.get("keys") or block.get("names") or []
            for ath in block.get("athletes", []):
                stats = ath.get("stats", []) or []
                athlete = ath.get("athlete", {}) or {}
                name = athlete.get("displayName")
                if not name or not stats:   # scratches carry no stats
                    continue
                vals = dict(zip(keys, stats))
                pos = ((athlete.get("position") or {}).get("abbreviation")
                       or "").upper()
                position = ("G" if default_pos == "G"
                            else "D" if pos == "D" else "F")
                pid = _to_num(athlete.get("id"))
                row = {"game_pk": event_id, "team": names[idx],
                       "opponent": opp, "name": name, "position": position,
                       "player_id": int(pid) if pid is not None else None}
                for k, field in fields.items():
                    v = _to_num(vals.get(k))
                    if v is not None:
                        row[field] = v
                if default_pos != "G":
                    g, a = row.get("goals"), row.get("assists")
                    if g is not None or a is not None:
                        row["points"] = (g or 0) + (a or 0)
                toi = _toi_seconds(vals.get("timeOnIce"))
                if toi is not None:
                    row["toi"] = toi
                rows.append(row)
    return rows


def box_player_logs(sport_key: str, event_id) -> list[dict]:
    """Per-player box-score lines for a finished game. Basketball: points,
    rebounds, assists, steals, blocks, threes. Football: passing/rushing/
    receiving/kicking stat lines. Hockey: skater goals/assists/points/shots/
    blocks/hits/pim and goalie saves/shots_against/goals_against.
    Returns [] on any issue."""
    try:
        data = _summary(sport_key, event_id)
    except Exception:
        return []
    path = SPORTS[sport_key].espn_path or ""
    if "football" in path:
        try:
            return _football_box(data, event_id)
        except Exception:
            return []
    if "hockey" in path:
        try:
            return _hockey_box(data, event_id)
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


# Tennis is player-vs-player and shaped differently from the team sports: a
# scoreboard "event" is a tournament whose ``groupings`` hold the individual
# matches (``competitions``) between two ``athlete`` competitors. ESPN carries no
# surface field, so we infer it from the tournament name (approximate, for the
# surface-aware Elo hook — it falls back to overall when unknown).
_GRASS = ("wimbledon", "halle", "terra wortmann", "queen", "hsbc championship",
          "eastbourne", "mallorca", "stuttgart", "hertogenbosch", "newport", "libema")
_CLAY = ("roland garros", "french open", "monte", "madrid", "rome", "italian open",
         "barcelona", "hamburg", "kitzbuhel", "gstaad", "bastad", "umag", "houston",
         "estoril", "munich", "bucharest", "cordoba", "santiago", "buenos aires", "rio")


def _tennis_surface(tournament: str) -> str:
    t = (tournament or "").lower()
    if any(k in t for k in _GRASS):
        return "grass"
    if any(k in t for k in _CLAY):
        return "clay"
    return "hard"


def _parse_tennis(data: dict) -> list[dict]:
    """Men's-singles matches from a tennis scoreboard payload: two players, the
    winner (if final), tournament, inferred surface, date, completion."""
    out = []
    for ev in data.get("events", []):
        tournament = ev.get("name") or ev.get("shortName") or ""
        surface = _tennis_surface(tournament)
        for grp in ev.get("groupings", []) or []:
            for c in grp.get("competitions", []) or []:
                if (c.get("type") or {}).get("slug") != "mens-singles":
                    continue
                comps = c.get("competitors") or []
                if len(comps) != 2:
                    continue
                players, winner = [], None
                for x in comps:
                    nm = (x.get("athlete") or {}).get("displayName")
                    if not nm:
                        break
                    players.append(nm)
                    if x.get("winner"):
                        winner = nm
                if len(players) != 2:
                    continue
                completed = bool((c.get("status") or {}).get("type", {}).get("completed"))
                out.append({
                    "match_id": c.get("id"),
                    "date": (c.get("date") or "")[:10],
                    "match_time": c.get("date"),
                    "tournament": tournament,
                    "surface": surface,
                    "player1": players[0],
                    "player2": players[1],
                    "winner": winner,
                    "completed": completed,
                })
    return out


def tennis_matches(sport_key: str, start: str, end: str,
                   completed_only: bool = False) -> list[dict]:
    """Men's-singles matches in [start, end] (chunked like results_range).
    ``completed_only`` keeps only finished matches (for Elo history); otherwise
    scheduled matches come through too (for a slate)."""
    from datetime import date, timedelta

    d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
    out, seen = [], set()
    while d0 <= d1:
        chunk_end = min(d0 + timedelta(days=149), d1)
        for data in _fetch_range("espn:tennis:v2", sport_key, d0, chunk_end):
            for m in _parse_tennis(data):
                if m["match_id"] in seen:
                    continue
                if completed_only and not (m["completed"] and m["winner"]):
                    continue
                seen.add(m["match_id"])
                out.append(m)
        d0 = chunk_end + timedelta(days=1)
    return out


# ESPN caps every scoreboard response at the request ``limit`` and silently
# drops the tail — the ``page`` param is accepted but ignored (verified live:
# page=2 returns the identical events). So multi-day fetches always request
# this high limit (overriding sp.espn_params limits meant for one-day slates,
# e.g. NCAAF's 400) and treat a response that reaches it as saturated.
_RANGE_LIMIT = 1000


def _fetch_range(cache_prefix: str, sport_key: str, d0, d1) -> list[dict]:
    """Raw scoreboard payloads covering [d0, d1] (datetime.date objects).
    A response with >= _RANGE_LIMIT events is saturated (ESPN truncated the
    tail), so the window is split in half recursively until each response is
    below the cap; a saturated single day can't be split further and is
    returned as-is (never observed — no league plays 1000 games in a day)."""
    from datetime import timedelta

    rng = f"{d0.strftime('%Y%m%d')}-{d1.strftime('%Y%m%d')}"
    data = cached_json(
        f"{cache_prefix}:{sport_key}:{rng}",
        _TTL_RESULTS,
        lambda: _get(sport_key, {"dates": rng, "limit": _RANGE_LIMIT}),
    )
    if len(data.get("events") or []) >= _RANGE_LIMIT and d0 < d1:
        mid = d0 + (d1 - d0) // 2
        return (_fetch_range(cache_prefix, sport_key, d0, mid)
                + _fetch_range(cache_prefix, sport_key,
                               mid + timedelta(days=1), d1))
    return [data]


def results_range(sport_key: str, start: str, end: str) -> list[dict]:
    """Completed regular-season and postseason games with final scores in
    [start, end]. ESPN rejects very long date ranges (observed 400s past ~1
    year), so wide windows are chunked into <=150-day requests and merged;
    each request saturating ESPN's response cap is split in half (see
    _fetch_range). Preseason and offseason/all-star events (season_type 1/4)
    are dropped so exhibition scores never reach ratings or Elo; events with
    no season type are kept."""
    from datetime import date, timedelta

    d0 = date.fromisoformat(start)
    d1 = date.fromisoformat(end)
    out: list[dict] = []
    seen: set = set()
    while d0 <= d1:
        chunk_end = min(d0 + timedelta(days=149), d1)
        for data in _fetch_range("espn:results:v2", sport_key, d0, chunk_end):
            for g in _parse_events(data):
                if (g["completed"] and g["home_score"] is not None
                        and g["season_type"] in (None, 2, 3)
                        and g["game_id"] not in seen):
                    seen.add(g["game_id"])
                    out.append(g)
        d0 = chunk_end + timedelta(days=1)
    return out
