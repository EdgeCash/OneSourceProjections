"""BettingPros Public Partner API client (https://api.bettingpros.com/v3).

Auth model (per the partner docs):
  - Every request: partner key in the `x-api-key` header.
  - Premium fields (projections, EV, recommended sides): add the query
    params `auth=user&user=<BP_USER>&key=<BP_USER_KEY>` to the request.
    Without all three, premium fields come back null.

Endpoints available to partners: /books, /events, /markets,
/markets/offer-counts, /offers, /props.

Rate limits: 5 req/sec (500 burst), 5,000 requests/day total. Responses
are disk-cached (see onesource/cache.py) and a client-side throttle keeps
us at <= 4 req/sec.
"""

from __future__ import annotations

import time

import requests

from .. import config
from ..cache import cached_json

BASE = "https://api.bettingpros.com/v3"
_TTL = 10 * 60  # lines move; keep this short
_MIN_INTERVAL = 0.26  # ~4 req/sec, under the 5 RPS cap
_last_request = 0.0


class BettingProsError(RuntimeError):
    pass


def _headers() -> dict:
    key = config.BP_PARTNER_KEY()
    if not key:
        raise BettingProsError("BP_PARTNER_KEY is not set")
    return {"x-api-key": key, "Accept": "application/json"}


def _premium_params() -> dict:
    """auth=user triple for premium-tier fields; empty dict if not set."""
    user, user_key = config.BP_USER(), config.BP_USER_KEY()
    if user and user_key:
        return {"auth": "user", "user": user, "key": user_key}
    return {}


def _get(path: str, params: dict, premium: bool = True) -> dict:
    global _last_request
    wait = _MIN_INTERVAL - (time.time() - _last_request)
    if wait > 0:
        time.sleep(wait)
    if premium:
        params = {**params, **_premium_params()}
    resp = requests.get(f"{BASE}/{path}", params=params, headers=_headers(), timeout=30)
    _last_request = time.time()
    if resp.status_code in (401, 403):
        raise BettingProsError(
            f"BettingPros auth failed ({resp.status_code}). Check BP_PARTNER_KEY. "
            f"Body: {resp.text[:300]}"
        )
    if resp.status_code == 429:
        raise BettingProsError("BettingPros rate limit hit (5 RPS / 5,000 per day)")
    resp.raise_for_status()
    return resp.json()


def markets(sport: str = "MLB") -> list[dict]:
    """Available markets for a sport — source of truth for market IDs."""
    data = cached_json(
        f"bp:markets:{sport}",
        24 * 60 * 60,
        lambda: _get("markets", {"sport": sport, "limit": 500}, premium=False),
    )
    return data.get("markets", [])


def market_lookup(sport: str) -> dict[int, dict]:
    """id -> {name, slug, category} for a sport, resolved live and cached."""
    out = {}
    for m in markets(sport):
        mid = m.get("id") or m.get("market_id")
        if mid is None:
            continue
        out[int(mid)] = {
            "name": m.get("name") or m.get("label") or "",
            "slug": m.get("slug") or m.get("market_slug") or "",
            "category": m.get("category") or m.get("market_category") or "",
        }
    return out


# Game-odds market slugs vary slightly by sport; try candidates in order.
_GAME_MARKET_SLUGS = {
    "moneyline": ["moneyline"],
    "total": ["over-under", "total", "totals", "total-points", "total-goals"],
    "spread": ["against-the-spread", "spread", "point-spread", "run-line",
               "puck-line"],
}


def game_market_ids(sport: str) -> dict[str, int | None]:
    """Resolve moneyline/total/spread market IDs for a sport at runtime."""
    by_slug = {info["slug"]: mid for mid, info in market_lookup(sport).items()}
    resolved = {}
    for market, candidates in _GAME_MARKET_SLUGS.items():
        resolved[market] = next(
            (by_slug[s] for s in candidates if s in by_slug), None
        )
    return resolved


def events(sport: str, date: str) -> list[dict]:
    """Events for a date. For MLB the payload includes lineups and park
    factors (lineups=true / park_factors=true are API defaults)."""
    data = cached_json(
        f"bp:events:{sport}:{date}",
        _TTL,
        lambda: _get("events", {"sport": sport, "date": date, "lineups": "true",
                                "park_factors": "true"}),
    )
    return data.get("events", [])


def offers(
    sport: str,
    market_id: int | str,
    event_ids: list[int] | None = None,
    location: str = "ALL",
) -> list[dict]:
    """Live odds offers (lines + selections per book) for a market.
    market_id accepts a single id or colon-delimited string of ids."""
    params: dict = {
        "sport": sport,
        "market_id": str(market_id),
        "location": location,
        "limit": 500,
    }
    if event_ids:
        params["event_id"] = ":".join(str(e) for e in event_ids)
    key = f"bp:offers:{sport}:{market_id}:{params.get('event_id', 'all')}:{location}"
    data = cached_json(key, _TTL, lambda: _get("offers", params))
    return data.get("offers", [])


def props(
    sport: str,
    date: str,
    market_ids: list[int] | None = None,
    location: str = "ALL",
) -> list[dict]:
    """BettingPros' own prop projections with EV and recommended sides
    (premium fields require the auth=user triple). We treat this as a
    second opinion next to our model, and as a line source."""
    params: dict = {
        "sport": sport,
        "date": date,
        "location": location,
        "limit": 500,
        "page": 1,
        "include_selections": "true",
        "include_markets": "false",
        "ev_threshold": "false",  # we want the full board, not just BP's edges
    }
    if market_ids:
        params["market_id"] = ":".join(str(m) for m in market_ids)
    key = f"bp:props:{sport}:{date}:{params.get('market_id', 'all')}:{location}"
    data = cached_json(key, _TTL, lambda: _get("props", params))
    return data.get("props", [])


def _dig(d: dict, *paths: str, default=None):
    """Pull the first present value from dot-separated paths."""
    for path in paths:
        cur: object = d
        for part in path.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                cur = None
                break
        if cur not in (None, ""):
            return cur
    return default


def flatten_offers(raw_offers: list[dict]) -> list[dict]:
    """Flatten the nested offer payload into one row per selection/book/line."""
    rows = []
    for offer in raw_offers:
        event_id = offer.get("event_id")
        market_id = offer.get("market_id")
        for selection in offer.get("selections", []):
            name = _dig(
                selection,
                "participant.name", "participant.player.name",
                "player.name", "label", "participant",
            )
            for book in selection.get("books", []):
                for line in book.get("lines", []):
                    rows.append(
                        {
                            "event_id": event_id,
                            "market_id": market_id,
                            "participant": name if isinstance(name, str) else None,
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


def flatten_props(raw_props: list[dict]) -> list[dict]:
    """One row per prop with BettingPros' projection / EV / recommendation.
    The prop object schema varies by tier, so every field is pulled
    defensively and missing values come back None."""
    rows = []
    for p in raw_props:
        name = _dig(p, "participant.name", "participant.player.name",
                    "player.name", "name")
        row = {
            "event_id": _dig(p, "event_id", "event.id"),
            "market_id": _dig(p, "market_id", "market.id"),
            "participant": name if isinstance(name, str) else None,
            "bp_line": _dig(p, "line", "selection.line", "over.line"),
            "bp_projection": _dig(p, "projection", "projection.value",
                                  "analysis.projection"),
            "bp_ev": _dig(p, "expected_value", "ev"),
            "bp_probability": _dig(p, "probability"),
            "bp_recommended_side": _dig(p, "recommended_side", "recommendation",
                                        "pick.side"),
            "bp_bet_rating": _dig(p, "bet_rating"),
            "over_line": None, "over_odds": None,
            "under_line": None, "under_odds": None,
        }
        # include_selections=true embeds over/under selections with book
        # lines; keep the best price for each side.
        for sel in p.get("selections") or []:
            label = str(sel.get("selection") or sel.get("label") or "").lower()
            side = "over" if "over" in label else "under" if "under" in label else None
            if not side:
                continue
            for book in sel.get("books") or []:
                for line in book.get("lines") or []:
                    cost = line.get("cost")
                    if cost is None or not line.get("active", True):
                        continue
                    if row[f"{side}_odds"] is None or cost > row[f"{side}_odds"]:
                        row[f"{side}_odds"] = cost
                        row[f"{side}_line"] = line.get("line")
        rows.append(row)
    return rows
