"""FantasyPros public API client. Used for daily MLB player projections,
which we blend with our own Statcast-derived rates for prop modeling."""

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


def mlb_projections(season: int, position: str | None = None) -> list[dict]:
    """Player projections for the season/slate. Position e.g. 'P' or 'H'."""
    params = {}
    if position:
        params["position"] = position
    data = cached_json(
        f"fp:mlb:proj:{season}:{position}",
        _TTL,
        lambda: _get(f"mlb/{season}/projections", params),
    )
    return data.get("players", [])


def projection_index(players: list[dict]) -> dict[str, dict]:
    """Map normalized player name -> projected stats dict."""
    from ..names import normalize

    out = {}
    for p in players:
        name = p.get("name") or p.get("player_name")
        stats = p.get("stats") or p.get("projections") or {}
        if name:
            out[normalize(name)] = stats
    return out
