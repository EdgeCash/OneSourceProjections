"""BettingPros API client (api.bettingpros.com/v3).

Auth uses your partner key in the `x-api-key` header; some endpoints also
accept the account user/key pair. If an endpoint shape differs on your
account tier, run scripts/discover_markets.py to inspect live responses —
every request/parse step here is small and easy to adjust.
"""

from __future__ import annotations

import requests

from .. import config
from ..cache import cached_json

BASE = "https://api.bettingpros.com/v3"
_TTL = 10 * 60  # lines move; keep this short


class BettingProsError(RuntimeError):
    pass


def _headers() -> dict:
    key = config.BP_PARTNER_KEY()
    if not key:
        raise BettingProsError("BP_PARTNER_KEY is not set")
    headers = {"x-api-key": key, "Accept": "application/json"}
    user, user_key = config.BP_USER(), config.BP_USER_KEY()
    if user and user_key:
        headers["x-bp-user"] = user
        headers["x-bp-user-key"] = user_key
    return headers


def _get(path: str, params: dict) -> dict:
    resp = requests.get(f"{BASE}/{path}", params=params, headers=_headers(), timeout=30)
    if resp.status_code in (401, 403):
        raise BettingProsError(
            f"BettingPros auth failed ({resp.status_code}). Check BP_PARTNER_KEY / "
            f"BP_USER / BP_USER_KEY. Body: {resp.text[:300]}"
        )
    resp.raise_for_status()
    return resp.json()


def markets(sport: str = "MLB") -> list[dict]:
    """List available markets — use to verify config.BP_MARKET_IDS."""
    data = _get("markets", {"sport": sport})
    return data.get("markets", data if isinstance(data, list) else [])


def events(sport: str, date: str) -> list[dict]:
    data = cached_json(
        f"bp:events:{sport}:{date}",
        _TTL,
        lambda: _get("events", {"sport": sport, "date": date}),
    )
    return data.get("events", [])


def offers(sport: str, market_id: int, event_ids: list[int] | None = None) -> list[dict]:
    """Offers (lines across books + consensus) for a market."""
    params: dict = {"sport": sport, "market_id": market_id, "location": "ALL"}
    if event_ids:
        params["event_id"] = ":".join(str(e) for e in event_ids)
    key = f"bp:offers:{sport}:{market_id}:{params.get('event_id', 'all')}"
    data = cached_json(key, _TTL, lambda: _get("offers", params))
    return data.get("offers", [])


def flatten_offers(raw_offers: list[dict]) -> list[dict]:
    """Flatten the nested offer payload into one row per selection/line with
    the best available price and the consensus (opening/current) odds."""
    rows = []
    for offer in raw_offers:
        event_id = offer.get("event_id")
        for selection in offer.get("selections", []):
            participant = selection.get("participant") or {}
            name = (
                participant.get("name")
                or (participant.get("player") or {}).get("name")
                or selection.get("label")
            )
            for book in selection.get("books", []):
                for line in book.get("lines", []):
                    rows.append(
                        {
                            "event_id": event_id,
                            "market_id": offer.get("market_id"),
                            "participant": name,
                            "selection": selection.get("selection")
                            or selection.get("label"),
                            "book_id": book.get("id"),
                            "line": line.get("line"),
                            "odds": line.get("cost"),
                            "is_best": line.get("best", False),
                            "active": line.get("active", True),
                        }
                    )
    return rows
