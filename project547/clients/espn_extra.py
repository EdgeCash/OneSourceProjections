"""Universal Sharp Sheets for in-season sports we don't model.

Free data + logos from ESPN's public scoreboard API (same source as
``clients/espn.py``, no key). For leagues we don't project, we still give a
clean, professional matchup sheet: team logos, records, recent form, league
rank, season stat comparison, odds and venue — everything the scoreboard
payload already carries, so it's one cached call per league per day.

The league registry below is just in-season *team-vs-team* leagues (soccer
today; add others freely). Individual sports (tennis/golf) use a different
shape and get their own layout later. Editing ``LEAGUES`` is all it takes to
add or drop a competition.
"""

from __future__ import annotations

from ..cache import cached_json
from .espn import BASE, _get_path  # reuse the shared host + fetch helper

_TTL = 15 * 60  # scoreboard refresh; cheap and rate-friendly

# key -> (espn_path, display name, group). Paths are ESPN's {sport}/{league}.
LEAGUES: dict[str, tuple[str, str, str]] = {
    # Soccer — clubs
    "mls":        ("soccer/usa.1",            "MLS",                "Soccer"),
    "epl":        ("soccer/eng.1",            "Premier League",     "Soccer"),
    "laliga":     ("soccer/esp.1",            "La Liga",            "Soccer"),
    "seriea":     ("soccer/ita.1",            "Serie A",            "Soccer"),
    "bundesliga": ("soccer/ger.1",            "Bundesliga",         "Soccer"),
    "ligue1":     ("soccer/fra.1",            "Ligue 1",            "Soccer"),
    "ligamx":     ("soccer/mex.1",            "Liga MX",            "Soccer"),
    "brasileirao": ("soccer/bra.1",           "Brasileirão",        "Soccer"),
    "ucl":        ("soccer/uefa.champions",   "Champions League",   "Soccer"),
    # Soccer — international (live in summer windows)
    "copa":       ("soccer/conmebol.america", "Copa América",       "Soccer"),
    "world_cup":  ("soccer/fifa.world",       "World Cup",          "Soccer"),
    "friendlies": ("soccer/fifa.friendly",    "Int'l Friendlies",   "Soccer"),
}


def leagues_by_group() -> dict[str, list[tuple[str, str]]]:
    """{group: [(key, display), ...]} for building a grouped picker."""
    out: dict[str, list[tuple[str, str]]] = {}
    for key, (_path, name, group) in LEAGUES.items():
        out.setdefault(group, []).append((key, name))
    return out


def _stat_list(competitor: dict) -> list[dict]:
    """Season stats ESPN attaches to a scoreboard competitor (name/value)."""
    out = []
    for s in competitor.get("statistics", []) or []:
        label = s.get("label") or s.get("name") or s.get("abbreviation")
        val = s.get("displayValue")
        if label and val is not None:
            out.append({"label": label, "value": val})
    return out


def _team(competitor: dict) -> dict:
    t = competitor.get("team") or {}
    return {
        "name": t.get("displayName") or t.get("name"),
        "abbr": t.get("abbreviation"),
        "logo": t.get("logo") or (t.get("logos") or [{}])[0].get("href"),
        "color": t.get("color"),
        "record": (competitor.get("records") or [{}])[0].get("summary"),
        "form": competitor.get("form"),          # e.g. "WWLDW"
        "rank": (competitor.get("curatedRank") or {}).get("current"),
        "score": competitor.get("score"),
        "stats": _stat_list(competitor),
    }


def _odds(comp: dict) -> dict | None:
    o = (comp.get("odds") or [None])[0]
    if not o:
        return None
    return {"details": o.get("details"), "over_under": o.get("overUnder"),
            "spread": o.get("spread"), "provider": (o.get("provider") or {}).get("name")}


def _parse_sheets(data: dict, label: str) -> list[dict]:
    games = []
    for ev in data.get("events", []) or []:
        comp = (ev.get("competitions") or [{}])[0]
        home = away = None
        for c in comp.get("competitors", []) or []:
            entry = _team(c)
            if c.get("homeAway") == "home":
                home = entry
            else:
                away = entry
        if not (home and away):
            continue
        status = (comp.get("status") or ev.get("status") or {}).get("type") or {}
        games.append({
            "league": label,
            "game_id": ev.get("id"),
            "name": ev.get("name") or f"{away['name']} at {home['name']}",
            "date": ev.get("date"),
            "state": status.get("state"),            # pre / in / post
            "detail": status.get("shortDetail"),     # "FT", "45'", "8:00 PM"
            "venue": (comp.get("venue") or {}).get("fullName"),
            "neutral": comp.get("neutralSite", False),
            "home": home, "away": away,
            "odds": _odds(comp),
        })
    return games


def games(league_key: str, date: str) -> list[dict]:
    """Sheet-ready matchups for a league on a date (YYYY-MM-DD)."""
    path, _name, _group = LEAGUES[league_key]
    compact = date.replace("-", "")
    data = cached_json(f"espn:sheet:{league_key}:{date}", _TTL,
                       lambda: _get_path(path, {"dates": compact}))
    return _parse_sheets(data, _name)
