"""Universal Sharp Sheets for every in-season sport we don't model.

Free data + logos from ESPN's public API (no key) so a user never has to leave
to research a sport we don't project. Handles all three ESPN event shapes:

  * team matchups   (soccer, CFL, college baseball, …) -> two-team Sharp Sheet
  * 1v1 events      (tennis, MMA)                       -> head-to-head sheet
  * field/leaderboard (golf, racing)                    -> schedule + field + odds

Competitors are parsed generically (``team`` *or* ``athlete``), so one parser
covers everything; the renderer branches on competitor count. The ``LEAGUES``
registry is just a dict — add or drop a competition in one line. Unknown or
out-of-season leagues simply return no events (handled gracefully upstream).
"""

from __future__ import annotations

from ..cache import cached_json
from .espn import _get_path  # shared host + fetch helper (site.api.espn.com)

_TTL = 15 * 60

# key -> (espn_path, display, group). Broad, in-season-aware coverage; ESPN
# returns nothing for a league with no events on a date, so breadth is free.
LEAGUES: dict[str, tuple[str, str, str]] = {
    # --- Soccer (clubs) ---
    "mls": ("soccer/usa.1", "MLS", "Soccer"),
    "nwsl": ("soccer/usa.nwsl", "NWSL", "Soccer"),
    "epl": ("soccer/eng.1", "Premier League", "Soccer"),
    "efl_champ": ("soccer/eng.2", "EFL Championship", "Soccer"),
    "laliga": ("soccer/esp.1", "La Liga", "Soccer"),
    "seriea": ("soccer/ita.1", "Serie A", "Soccer"),
    "bundesliga": ("soccer/ger.1", "Bundesliga", "Soccer"),
    "ligue1": ("soccer/fra.1", "Ligue 1", "Soccer"),
    "eredivisie": ("soccer/ned.1", "Eredivisie", "Soccer"),
    "primeira": ("soccer/por.1", "Primeira Liga", "Soccer"),
    "ligamx": ("soccer/mex.1", "Liga MX", "Soccer"),
    "brasileirao": ("soccer/bra.1", "Brasileirão", "Soccer"),
    "ucl": ("soccer/uefa.champions", "Champions League", "Soccer"),
    "uel": ("soccer/uefa.europa", "Europa League", "Soccer"),
    # --- Soccer (international) ---
    "copa": ("soccer/conmebol.america", "Copa América", "Soccer"),
    "euros": ("soccer/uefa.euro", "UEFA Euro", "Soccer"),
    "world_cup": ("soccer/fifa.world", "World Cup", "Soccer"),
    "wwc": ("soccer/fifa.wwc", "Women's World Cup", "Soccer"),
    "friendlies": ("soccer/fifa.friendly", "Int'l Friendlies", "Soccer"),
    # --- Football ---
    "cfl": ("football/cfl", "CFL", "Football"),
    "ufl": ("football/ufl", "UFL", "Football"),
    # --- Baseball ---
    "college_baseball": ("baseball/college-baseball", "College Baseball", "Baseball"),
    # --- Basketball ---
    "mcbb": ("basketball/mens-college-basketball", "Men's College Hoops", "Basketball"),
    "wcbb": ("basketball/womens-college-basketball", "Women's College Hoops", "Basketball"),
    "gleague": ("basketball/nba-development", "NBA G League", "Basketball"),
    # --- Hockey ---
    "college_hockey": ("hockey/mens-college-hockey", "College Hockey", "Hockey"),
    # --- Tennis (1v1) ---
    "atp": ("tennis/atp", "ATP Tour", "Tennis"),
    "wta": ("tennis/wta", "WTA Tour", "Tennis"),
    # --- Golf (field) ---
    "pga": ("golf/pga", "PGA Tour", "Golf"),
    "lpga": ("golf/lpga", "LPGA Tour", "Golf"),
    "liv": ("golf/liv", "LIV Golf", "Golf"),
    # --- MMA / Combat (1v1 cards) ---
    "ufc": ("mma/ufc", "UFC", "MMA"),
    "pfl": ("mma/pfl", "PFL", "MMA"),
    # --- Motorsport (field) ---
    "f1": ("racing/f1", "Formula 1", "Motorsport"),
    "nascar": ("racing/nascar-premier", "NASCAR Cup", "Motorsport"),
    "indycar": ("racing/irl", "IndyCar", "Motorsport"),
    # --- Other team sports ---
    "afl": ("australian-football/afl", "AFL", "Aussie Rules"),
    "pll": ("lacrosse/pll", "PLL", "Lacrosse"),
}


def leagues_by_group() -> dict[str, list[tuple[str, str]]]:
    """{group: [(key, display), ...]} for a grouped picker."""
    out: dict[str, list[tuple[str, str]]] = {}
    for key, (_path, name, group) in LEAGUES.items():
        out.setdefault(group, []).append((key, name))
    return out


def _stat_list(competitor: dict) -> list[dict]:
    out = []
    for s in competitor.get("statistics", []) or []:
        label = s.get("label") or s.get("name") or s.get("abbreviation")
        val = s.get("displayValue")
        if label and val is not None:
            out.append({"label": label, "value": val})
    return out


def _competitor(c: dict) -> dict:
    """Generic: a competitor is a team OR an athlete (tennis/MMA/golf)."""
    t = c.get("team") or {}
    ath = c.get("athlete") or (c.get("athletes") or [{}])[0] or {}
    logo = (t.get("logo") or (t.get("logos") or [{}])[0].get("href")
            or (ath.get("flag") or {}).get("href")
            or (ath.get("headshot") or {}).get("href"))
    return {
        "name": t.get("displayName") or ath.get("displayName") or ath.get("fullName"),
        "abbr": t.get("abbreviation") or ath.get("shortName"),
        "logo": logo,
        "color": t.get("color"),
        "record": (c.get("records") or [{}])[0].get("summary"),
        "form": c.get("form"),
        "rank": (c.get("curatedRank") or {}).get("current"),
        "score": c.get("score"),
        "homeAway": c.get("homeAway"),
        "winner": c.get("winner"),
        "stats": _stat_list(c),
    }


def _odds(comp: dict) -> dict | None:
    o = (comp.get("odds") or [None])[0]
    if not o:
        return None
    def _ml(side):  # ESPN nests per-side moneylines here when priced
        return (o.get(side) or {}).get("moneyLine")
    return {"details": o.get("details"), "over_under": o.get("overUnder"),
            "spread": o.get("spread"),
            "home_ml": _ml("homeTeamOdds"), "away_ml": _ml("awayTeamOdds"),
            "draw_ml": o.get("drawOdds"),
            "provider": (o.get("provider") or {}).get("name")}


def _parse_events(data: dict, label: str) -> list[dict]:
    out = []
    for ev in data.get("events", []) or []:
        comp = (ev.get("competitions") or [{}])[0]
        comps = [_competitor(c) for c in comp.get("competitors", []) or []]
        comps = [c for c in comps if c.get("name")]
        if not comps:
            continue
        home = away = None
        if len(comps) == 2:
            x, y = comps
            if y.get("homeAway") == "home":
                home, away = y, x
            else:  # x is home, or no homeAway (1v1) -> stable order
                home, away = x, y
        status = (comp.get("status") or ev.get("status") or {}).get("type") or {}
        out.append({
            "league": label,
            "game_id": ev.get("id"),
            "name": ev.get("name") or ev.get("shortName") or (
                f"{away['name']} @ {home['name']}" if home and away else label),
            "date": ev.get("date"),
            "state": status.get("state"),
            "detail": status.get("shortDetail"),
            "venue": (comp.get("venue") or {}).get("fullName"),
            "neutral": comp.get("neutralSite", False),
            "competitors": comps,
            "home": home, "away": away,            # None for field/leaderboard
            "is_matchup": len(comps) == 2,
            "odds": _odds(comp),
        })
    return out


def events(league_key: str, date: str) -> list[dict]:
    """All events for a league on a date (YYYY-MM-DD): matchups and fields."""
    path, _name, _group = LEAGUES[league_key]
    compact = date.replace("-", "")
    data = cached_json(f"espn:sheet:{league_key}:{date}", _TTL,
                       lambda: _get_path(path, {"dates": compact}))
    return _parse_events(data, _name)
