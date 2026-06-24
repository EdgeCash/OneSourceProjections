"""FantasyPros Public API client (api.fantasypros.com/public/v2/json).

We use the MLB daily projections (`type=daily&date=YYYY-MM-DD`) — per-game
projected lines for hitters (H) and pitchers (P) — which feed straight
into the prop models. Note the response's player array key is `player`.
"""

from __future__ import annotations

import requests

from .. import config
from ..cache import cached_json

BASE = "https://api.fantasypros.com/public/v2/json"
_TTL = 60 * 60


class FantasyProsError(RuntimeError):
    pass


def _get(path: str, params: dict | None = None) -> dict:
    key = config.FANTASYPROS_API_KEY()
    if not key:
        raise FantasyProsError("FANTASYPROS_API_KEY is not set")
    resp = requests.get(
        f"{BASE}/{path}",
        params=params or {},
        headers={"x-api-key": key, "Accept": "application/json"},
        timeout=30,
    )
    if resp.status_code in (401, 403):
        raise FantasyProsError(
            f"FantasyPros auth failed ({resp.status_code}): {resp.text[:300]}"
        )
    resp.raise_for_status()
    return resp.json()


def mlb_projections(
    season: int,
    proj_type: str = "daily",
    date: str | None = None,
    position: str | None = None,
) -> list[dict]:
    """MLB player projections. proj_type: daily | weekly | ros | preseason.
    Daily projections are per-game stat lines for the given date."""
    params: dict = {"type": proj_type}
    if date:
        params["date"] = date
    if position:
        params["position"] = position
    data = cached_json(
        f"fp:mlb:proj:{season}:{proj_type}:{date}:{position}",
        _TTL,
        lambda: _get(f"mlb/{season}/projections", params),
    )
    return data.get("player", [])


def nba_projections(season: int, date: str | None = None) -> list[dict]:
    """NBA daily player projections (PTS/REB/AST etc.)."""
    params: dict = {"type": "daily", "stat_values": "precise"}
    if date:
        params["date"] = date
    data = cached_json(
        f"fp:nba:proj:{season}:{date}",
        _TTL,
        lambda: _get(f"nba/{season}/projections", params),
    )
    return data.get("player", [])


def nfl_projections(season: int, week: int, position: str = "ALL") -> list[dict]:
    """NFL weekly player projections."""
    data = cached_json(
        f"fp:nfl:proj:{season}:{week}:{position}",
        _TTL,
        lambda: _get(
            f"nfl/{season}/projections", {"week": week, "position": position}
        ),
    )
    return data.get("players", data.get("player", []))


def mlb_lineups(date: str, projected: bool = True) -> list[dict]:
    """Confirmed or projected MLB lineups for a date."""
    data = cached_json(
        f"fp:mlb:lineups:{date}:{projected}",
        30 * 60,
        lambda: _get(
            "mlb/lineups",
            {"start": date, "projected": "true" if projected else "false"},
        ),
    )
    return data.get("games", [])


def projection_index(players: list[dict]) -> dict[str, dict]:
    """Map normalized player name -> projected stats dict."""
    from ..names import normalize

    out = {}
    for p in players:
        name = p.get("name") or p.get("player_name")
        stats = p.get("stats") or p.get("projections") or {
            k: v for k, v in p.items() if isinstance(v, (int, float))
        }
        if name:
            out[normalize(name)] = stats
    return out


def news(sport: str, limit: int = 20) -> list[dict]:
    """Player news items (breaking/injury/transaction) for a sport."""
    data = cached_json(
        f"fp:news:{sport}:{limit}", 30 * 60,
        lambda: _get(f"{sport.lower()}/news", {"limit": limit}),
    )
    return data.get("items", [])


def injuries(sport: str, year: int | None = None) -> list[dict]:
    """Current injury report for a sport."""
    params = {"year": year} if year else {}
    data = cached_json(
        f"fp:injuries:{sport}:{year}", 60 * 60,
        lambda: _get(f"{sport.lower()}/injuries", params),
    )
    return data.get("injuries", [])
