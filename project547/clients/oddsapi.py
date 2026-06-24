"""The Odds API (the-odds-api.com) — multi-book game odds for line shopping
and a sharper, market-consensus closing line for CLV.

Credit-frugal by design (the account has a hard monthly cap):
  - game lines only (h2h / totals / spreads); player props stay on
    BettingPros, which would be far too credit-expensive here;
  - one request per sport per hour, disk-cached, so reruns don't re-spend;
  - a persisted credit floor: once the account's remaining credits drop
    below ODDS_API_MIN_CREDITS we stop calling, so it can never drain to 0.

A request costs (markets × regions) credits; with us-region h2h+totals+spreads
that's 3 credits per sport per call. Failed requests (4xx) are not charged.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import requests

from .. import config
from ..cache import cached_json
from ..config import CACHE_DIR

log = logging.getLogger(__name__)

BASE = "https://api.the-odds-api.com/v4"

# our sport key -> The Odds API sport key
SPORT_KEYS = {
    "MLB": "baseball_mlb",
    "WNBA": "basketball_wnba",
    "NBA": "basketball_nba",
    "NHL": "icehockey_nhl",
    "NCAAF": "americanfootball_ncaaf",
    "NFL": "americanfootball_nfl",
}

# The Odds API market key -> our internal market name
_MARKET = {"h2h": "moneyline", "totals": "total", "spreads": "spread"}

_BUDGET_FILE = CACHE_DIR / "oddsapi_budget.json"


# ---------------------------------------------------------------------------
# Credit budget tracking (persisted so the floor survives across processes)
# ---------------------------------------------------------------------------

def _read_budget() -> dict:
    try:
        return json.loads(_BUDGET_FILE.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def credits_remaining() -> int | None:
    return _read_budget().get("remaining")


def _record_budget(headers) -> None:
    rem = headers.get("x-requests-remaining")
    used = headers.get("x-requests-used")
    if rem is None:
        return
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _BUDGET_FILE.write_text(json.dumps({
        "remaining": int(float(rem)),
        "used": int(float(used)) if used is not None else None,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }))


def _below_floor() -> bool:
    rem = credits_remaining()
    return rem is not None and rem < config.ODDS_API_MIN_CREDITS


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def _fetch(osk: str, regions: str, markets: str) -> list[dict]:
    resp = requests.get(
        f"{BASE}/sports/{osk}/odds",
        params={"apiKey": config.THE_ODDS_API_KEY(), "regions": regions,
                "markets": markets, "oddsFormat": "american"},
        timeout=30)
    resp.raise_for_status()
    _record_budget(resp.headers)
    return resp.json()


def game_odds(sport_key: str, regions: str | None = None,
              markets: str | None = None, ttl: int | None = None) -> list[dict]:
    """Raw events with multi-book odds for a sport, or [] when unavailable
    (no key, unmapped sport, credit floor reached, or API error). Cached for
    ttl seconds so repeated calls within an hour don't spend credits."""
    if not config.THE_ODDS_API_KEY():
        return []
    osk = SPORT_KEYS.get(sport_key)
    if not osk:
        return []
    if _below_floor():
        log.warning("Odds API credit floor reached (%s left); skipping %s",
                    credits_remaining(), sport_key)
        return []
    regions = regions or config.ODDS_API_REGIONS
    markets = markets or config.ODDS_API_MARKETS
    ttl = config.ODDS_API_TTL if ttl is None else ttl
    bucket = datetime.now(timezone.utc).strftime("%Y%m%d%H")
    key = f"oddsapi:{osk}:{regions}:{markets}:{bucket}"
    try:
        return cached_json(key, ttl, lambda: _fetch(osk, regions, markets))
    except Exception as e:
        log.warning("Odds API fetch failed for %s: %s", sport_key, e)
        return []


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def normalize(events: list[dict]) -> list[dict]:
    """Flatten events into one row per book/market/outcome."""
    rows = []
    for ev in events or []:
        home, away = ev.get("home_team"), ev.get("away_team")
        for bk in ev.get("bookmakers", []) or []:
            for mk in bk.get("markets", []) or []:
                market = _MARKET.get(mk.get("key"))
                if not market:
                    continue
                for oc in mk.get("outcomes", []) or []:
                    rows.append({
                        "event_id": ev.get("id"), "home_team": home,
                        "away_team": away, "commence_time": ev.get("commence_time"),
                        "book": bk.get("key"), "market": market,
                        "name": oc.get("name"), "price": oc.get("price"),
                        "point": oc.get("point"),
                    })
    return rows


def snapshot_rows(events: list[dict], sport: str, date: str,
                  captured: str) -> list[dict]:
    """Map multi-book odds into the snapshot schema the CLV reader consumes
    (one row per book/market/side, tagged source='oddsapi')."""
    rows = []
    for r in normalize(events):
        row = {"event_id": f"oa:{r['event_id']}", "book_id": r["book"],
               "market": r["market"], "odds": r["price"], "line": r.get("point"),
               "captured_at": captured, "sport": sport, "date": date,
               "kind": "game", "source": "oddsapi"}
        if r["market"] == "total":
            row["selection"] = r["name"]            # "Over" / "Under"
        else:
            row["participant"] = r["name"]          # team name
        rows.append(row)
    return rows


def best_prices(rows: list[dict]) -> dict:
    """Best (longest) available price per outcome across books — the line-
    shopping view. Keyed by (event_id, market, name, point)."""
    best: dict = {}
    for r in rows:
        if r.get("price") is None:
            continue
        k = (r["event_id"], r["market"], r["name"], r.get("point"))
        if k not in best or r["price"] > best[k]["price"]:
            best[k] = {"price": r["price"], "book": r["book"],
                       "home_team": r["home_team"], "away_team": r["away_team"],
                       "point": r.get("point")}
    return best
