"""BettingPros Public Partner API client (https://api.bettingpros.com/v3).

Auth model (per the partner docs):
  - Every request: partner key in the `x-api-key` header.
  - Premium fields (projections, EV, recommended sides): add the query
    params `auth=user&user=<BP_USER>&key=<BP_USER_KEY>` to the request.
    Without all three, premium fields come back null.

Endpoints available to partners: /books, /events, /markets,
/markets/offer-counts, /offers, /props.

Rate limits: 5 req/sec (500 burst), 5,000 requests/day total. Responses
are disk-cached (see project547/cache.py) and a client-side throttle keeps
us at <= 4 req/sec.
"""

from __future__ import annotations

import logging
import time

import requests

from .. import config
from ..cache import cached_json

log = logging.getLogger(__name__)

BASE = "https://api.bettingpros.com/v3"
_TTL = 10 * 60  # lines move; keep this short
_MIN_INTERVAL = 0.26  # ~4 req/sec, under the 5 RPS cap
_last_request = 0.0

# Sports for which BettingPros serves SGP correlated-pick suggestions. Asking
# for them on any other sport corrupts the /props response (see props()).
CORRELATED_PICK_SPORTS = {"NFL", "NBA"}


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


def books(sport: str = "MLB") -> list[dict]:
    """All sportsbooks BettingPros tracks for a sport — the source of truth for
    book IDs (incl. DFS operators like PrizePicks/Underdog when offered)."""
    data = cached_json(
        f"bp:books:{sport}",
        24 * 60 * 60,
        lambda: _get("books", {"sport": sport}, premium=False),
    )
    return data.get("books", [])


def book_lookup(sport: str = "MLB") -> dict[int, str]:
    """book_id -> name, resolved live and cached."""
    out = {}
    for b in books(sport):
        bid = b.get("id") or b.get("book_id")
        if bid is None:
            continue
        out[int(bid)] = (b.get("name") or b.get("book_name")
                         or b.get("display_name") or "")
    return out


def market_lookup(sport: str) -> dict[int, dict]:
    """id -> {name, slug, category} for a sport, resolved live and cached."""
    out = {}
    for m in markets(sport):
        mid = m.get("id") or m.get("market_id")
        if mid is None:
            continue
        out[int(mid)] = {
            "name": m.get("name") or m.get("market_name") or m.get("label")
            or m.get("display_name") or "",
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
    season: int | None = None,
) -> list[dict]:
    """Live odds offers (lines + selections per book) for a market.

    The exact accepted parameter combination varies (live runs got 400s on
    our first guess), so this tries a sequence of variants and remembers the
    first one that works for the rest of the process. Docs say offers wants
    market_id and *either* event_id or season, so one variant swaps the
    event filter for season (rows are filtered by event downstream anyway).
    """
    key = f"bp:offers:{sport}:{market_id}:{':'.join(map(str, event_ids or []))[:60]}:{location}"
    data = cached_json(key, _TTL,
                       lambda: _offers_attempts(sport, market_id, event_ids,
                                                location, season))
    return data.get("offers", [])


_OFFERS_STYLE: dict = {"idx": None}


def _offers_variants(event_ids, location, season):
    ev = {"event_id": ":".join(str(e) for e in event_ids)} if event_ids else {}
    season_d = {"season": season} if season else {}
    return [
        {**ev, "location": location, "limit": 100, "page": 1},
        {**ev, "location": location},
        {**ev},
        {**season_d, "location": location, "limit": 100, "page": 1},
        {**ev, "location": "NJ", "limit": 100, "page": 1},
    ]


def _offers_attempts(sport, market_id, event_ids, location, season) -> dict:
    import requests as _rq

    base = {"sport": sport, "market_id": str(market_id)}
    variants = _offers_variants(event_ids, location, season)
    order = list(range(len(variants)))
    if _OFFERS_STYLE["idx"] is not None:
        order.remove(_OFFERS_STYLE["idx"])
        order.insert(0, _OFFERS_STYLE["idx"])
    last_err: Exception | None = None
    last_data: dict | None = None
    for i in order:
        try:
            data = _get("offers", {**base, **variants[i]})
        except _rq.HTTPError as e:
            if e.response is not None and e.response.status_code == 400:
                last_err = e
                continue
            raise
        # A 200 with zero offers is not success — a different param style may
        # return rows (e.g. game-market style returns empty for prop markets).
        # Only lock in / return a style that actually produced offers; keep the
        # first empty response as a fallback if every variant comes back empty.
        if data.get("offers"):
            _OFFERS_STYLE["idx"] = i
            return data
        if last_data is None:
            last_data = data
    if last_data is not None:
        return last_data  # genuinely no offers for this market right now
    raise BettingProsError(f"offers 400 on all param variants: {last_err}")


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
    # BettingPros only supports SGP correlations for NFL and NBA. Asking for them
    # on any other sport makes the API replace the ENTIRE props list with a
    # warning string ("Unsupported sports found for SGP correlations..."), which
    # silently empties the board — so only request them where they're supported.
    if sport.upper() in CORRELATED_PICK_SPORTS:
        params["include_correlated_picks"] = "true"
        params["correlated_picks_limit"] = 6
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
    """Flatten the nested offer payload into one row per selection/book/line.
    Pre-flattened rows (replayed from the snapshot library) pass through."""
    if raw_offers and isinstance(raw_offers[0], dict) \
            and "odds" in raw_offers[0] and "selections" not in raw_offers[0]:
        return raw_offers
    rows = []
    for offer in raw_offers:
        event_id = offer.get("event_id")
        market_id = offer.get("market_id")
        player_id = offer.get("player_id") or _dig(offer, "participant.id",
                                                   "participant.player.id")
        # Player props nest the player at the OFFER level (participant/player);
        # the SELECTION only carries the side ("Over"/"Under"). Game offers carry
        # the team at the selection level. So for player props (offer has a
        # player_id) the name must come from the offer level (or be backfilled
        # from player_id later) — never from the selection's side label.
        offer_name = _dig(offer, "participant.name", "participant.player.name",
                          "player.name", "player.full_name")
        for selection in offer.get("selections", []):
            if player_id is not None:
                name = offer_name      # player prop — never the "Over"/"Under" label
            else:
                name = _dig(
                    selection,
                    "participant.name", "participant.player.name",
                    "player.name", "label", "participant",
                ) or offer_name
            for book in selection.get("books", []):
                for line in book.get("lines", []):
                    rows.append(
                        {
                            "event_id": event_id,
                            "market_id": market_id,
                            "player_id": player_id,
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
    if raw_props and isinstance(raw_props[0], dict) \
            and "bp_line" in raw_props[0] and "projection" not in raw_props[0]:
        return raw_props  # pre-flattened (replayed from the snapshot library)
    rows = []
    for p in raw_props:
        name = _dig(p, "participant.name", "participant.player.name",
                    "player.name", "participant", "name")
        # Live payloads nest the premium fields inside a `projection` dict:
        # {recommended_side, value, probability, expected_value, bet_rating,
        #  diff}. Fall back to flat fields for older/other shapes.
        proj = p.get("projection")
        nested = proj if isinstance(proj, dict) else {}
        row = {
            "event_id": _dig(p, "event_id", "event.id"),
            "market_id": _dig(p, "market_id", "market.id"),
            "participant": name if isinstance(name, str) else None,
            "bp_line": _dig(p, "line", "selection.line", "over.line"),
            "bp_projection": _num_or_none(nested.get("value"))
            if nested else _num_or_none(_dig(p, "projection", "analysis.projection")),
            "bp_ev": _num_or_none(nested.get("expected_value",
                                             _dig(p, "expected_value", "ev"))),
            "bp_probability": _num_or_none(nested.get("probability",
                                                      _dig(p, "probability"))),
            "bp_recommended_side": nested.get("recommended_side")
            or _dig(p, "recommended_side", "recommendation", "pick.side"),
            "bp_bet_rating": _num_or_none(nested.get("bet_rating",
                                                     _dig(p, "bet_rating"))),
            "bp_diff": _num_or_none(nested.get("diff")),
            "over_line": None, "over_odds": None,
            "under_line": None, "under_odds": None,
        }
        # player metadata (team / position / headshot) when present
        player = (p.get("participant") or {})
        if isinstance(player, dict):
            meta = player.get("player") or {}
            row["player_team"] = meta.get("team")
            row["player_position"] = meta.get("position")
            row["player_image"] = meta.get("image")
        # direct over/under objects: best line/odds, consensus, and the
        # opening price (open->close CLV per prop, captured every snapshot)
        for side in ("over", "under"):
            sd = p.get(side)
            if isinstance(sd, dict):
                row[f"{side}_line"] = _num_or_none(sd.get("line", row["bp_line"]))
                row[f"{side}_odds"] = _num_or_none(sd.get("cost", sd.get("odds")))
                row[f"{side}_consensus"] = _num_or_none(sd.get("consensus_odds"))
                opening = (sd.get("selection") or {}).get("opening_line") or {}
                row[f"{side}_open"] = _num_or_none(opening.get("cost"))
        if row["bp_line"] is None:
            row["bp_line"] = row.get("over_line")
        # BettingPros' own correlated-pick suggestions for same-game parlays
        # (include_correlated_picks=true — adds no extra request).
        row["correlated_picks"] = _correlated_picks(p)
        # public consensus: BettingPros pick counts per side
        def _picks(sd):
            pk = ((sd or {}).get("selection") or {}).get("picks") or {}
            return sum(v for v in pk.values() if isinstance(v, (int, float)))
        o_picks, u_picks = _picks(p.get("over")), _picks(p.get("under"))
        row["picks_total"] = (o_picks + u_picks) or None
        row["pick_pct_over"] = (round(o_picks / (o_picks + u_picks), 3)
                                if (o_picks + u_picks) > 0 else None)
        # opponent defensive rank vs this market
        opp = ((p.get("extra") or {}).get("opposition_rank") or {})
        row["opp_rank"] = _num_or_none(opp.get("rank"))
        # BettingPros' own over/under records by window -> over-rates
        perf = p.get("performance") or {}
        for window, key in (("last_5", "perf_l5"), ("last_10", "perf_l10"),
                            ("last_20", "perf_l20"), ("season", "perf_season"),
                            ("h2h", "perf_h2h")):
            w = perf.get(window) or {}
            o, u = w.get("over", 0) or 0, w.get("under", 0) or 0
            row[key] = round(o / (o + u), 3) if (o + u) > 0 else None
        row["streak"] = perf.get("streak")
        row["streak_type"] = perf.get("streak_type")
        # include_selections=true embeds over/under selections with book
        # lines; keep the best price for each side. Selection shapes vary,
        # so probe both nested books->lines and flat cost/line fields.
        for sel in p.get("selections") or []:
            label = str(sel.get("selection") or sel.get("label") or "").lower()
            side = "over" if "over" in label else "under" if "under" in label else None
            if not side:
                continue
            flat_cost = _num_or_none(sel.get("cost", sel.get("odds")))
            if flat_cost is not None:
                if row[f"{side}_odds"] is None or flat_cost > row[f"{side}_odds"]:
                    row[f"{side}_odds"] = flat_cost
                    row[f"{side}_line"] = _num_or_none(sel.get("line", row["bp_line"]))
            for book in sel.get("books") or []:
                for line in book.get("lines") or []:
                    cost = _num_or_none(line.get("cost"))
                    if cost is None or not line.get("active", True):
                        continue
                    if row[f"{side}_odds"] is None or cost > row[f"{side}_odds"]:
                        row[f"{side}_odds"] = cost
                        row[f"{side}_line"] = _num_or_none(line.get("line"))
        rows.append(row)
    return rows


def _num_or_none(v):
    if v is None or isinstance(v, (dict, list)):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# DFS operators carried by BettingPros' props feed. Matched by book name
# (case-insensitive) so we don't depend on guessing book_ids.
DFS_BOOK_NAMES = {"prizepicks", "underdog", "dabble", "sleeper", "betr"}

# Confirmed book ids from the live /books directory (see
# data/history/raw/bp_books_*.json). The pick'em line lives in the per-book
# /offers breakdown, not the consensus /props board.
DFS_BOOK_IDS = {
    37: "prizepicks",
    36: "underdog",
    45: "betr",
    63: "sleeper",
    53: "dabble",
}
PRIZEPICKS_BOOK_ID = 37
UNDERDOG_BOOK_ID = 36


def dfs_offer_lines(offer_rows: list[dict],
                    book_ids: dict[int, str] | None = None) -> list[dict]:
    """Pivot flattened /offers rows (from ``flatten_offers``) into one row per
    player+market+DFS-book with that book's own over/under line + odds. The DFS
    pick'em line often differs from the consensus, and that gap is the edge.

    Offer rows carry ``book_id`` but no name, so the DFS books are identified by
    id (``DFS_BOOK_IDS`` by default) and tagged with their operator name.
    """
    ids = book_ids or DFS_BOOK_IDS
    grouped: dict[tuple, dict] = {}
    for r in offer_rows or []:
        bid = r.get("book_id")
        if bid not in ids:
            continue
        if not r.get("active", True):
            continue
        line, oddv = r.get("line"), r.get("odds")
        if line is None or oddv is None:
            continue
        key = (r.get("event_id"), r.get("market_id"),
               r.get("participant"), bid)
        rec = grouped.setdefault(key, {
            "event_id": r.get("event_id"), "market_id": r.get("market_id"),
            "market": r.get("market"), "participant": r.get("participant"),
            "book_id": bid, "book_name": ids[bid],
            "over_line": None, "over_odds": None,
            "under_line": None, "under_odds": None,
        })
        side = str(r.get("selection") or "").lower()
        if "over" in side:
            rec["over_line"], rec["over_odds"] = _num_or_none(line), _num_or_none(oddv)
        elif "under" in side:
            rec["under_line"], rec["under_odds"] = _num_or_none(line), _num_or_none(oddv)
    return list(grouped.values())


def prop_book_lines(raw_props: list[dict]) -> list[dict]:
    """One row per prop / book / side with that book's own line + odds —
    including DFS operators (PrizePicks, Underdog, …) when BettingPros returns
    them. Reads the same selections[].books[].lines[] nesting flatten_props
    already iterates, but keeps the book identity instead of collapsing to the
    best price."""
    rows = []
    for p in raw_props or []:
        if not isinstance(p, dict):
            continue
        event_id = _dig(p, "event_id", "event.id")
        market_id = _dig(p, "market_id", "market.id")
        name = _dig(p, "participant.name", "participant.player.name",
                    "player.name", "participant", "name")
        name = name if isinstance(name, str) else None
        for sel in p.get("selections") or []:
            label = str(sel.get("selection") or sel.get("label") or "").lower()
            side = "over" if "over" in label else "under" if "under" in label else None
            if not side:
                continue
            for book in sel.get("books") or []:
                bname = (book.get("name") or "")
                for line in book.get("lines") or []:
                    if not line.get("active", True):
                        continue
                    rows.append({
                        "event_id": event_id, "market_id": market_id,
                        "participant": name, "side": side,
                        "book_id": book.get("id"),
                        "book_name": bname.lower() or None,
                        "line": _num_or_none(line.get("line")),
                        "odds": _num_or_none(line.get("cost", line.get("odds"))),
                    })
    return rows


def dfs_prop_lines(raw_props: list[dict],
                   book_ids: set[int] | None = None) -> list[dict]:
    """The PrizePicks/Underdog (and similar) per-prop lines, filtered from the
    per-book rows by book name or explicit book_ids."""
    out = []
    for r in prop_book_lines(raw_props):
        if (book_ids and r["book_id"] in book_ids) or (r["book_name"] in DFS_BOOK_NAMES):
            out.append(r)
    return out


def _correlated_picks(p: dict) -> list[dict]:
    """Flatten BettingPros' correlated-pick suggestions (from
    include_correlated_picks=true) into ``[{player, market, side, line, odds,
    correlation}]``. The nested shape isn't documented field-by-field, so every
    field is pulled defensively from several candidate paths and missing values
    come back None; an unexpected shape yields an empty list rather than an
    error."""
    raw = p.get("correlated_picks") or p.get("correlated") or []
    if not isinstance(raw, list):
        return []
    out = []
    for c in raw:
        if not isinstance(c, dict):
            continue
        name = _dig(c, "participant.name", "participant.player.name",
                    "player.name", "participant", "name", "label")
        side = (c.get("recommended_side") or c.get("side")
                or _dig(c, "selection.label", "pick.side"))
        out.append({
            "player": name if isinstance(name, str) else None,
            "market": _dig(c, "market.name", "market_name", "market", "market_id"),
            "side": side if isinstance(side, str) else None,
            "line": _num_or_none(_dig(c, "line", "selection.line")),
            "odds": _num_or_none(_dig(c, "cost", "odds", "selection.cost")),
            "correlation": _num_or_none(
                _dig(c, "correlation", "correlation_coefficient",
                     "correlation_value", "r")),
        })
    return out


# Resolve our prop-market names to live BettingPros market ids by keyword
# match on the /markets names/slugs (ids differ per sport and aren't stable
# enough to hardcode). Excluded words avoid team/derivative variants.
_PROP_KEYWORDS = {
    "MLB": {
        "pitcher_strikeouts": (("strikeout",), ("team", "first", "alt")),
        "batter_hits": (("hits",), ("team", "allowed", "runs", "rbis", "alt")),
        "batter_total_bases": (("total bases",), ("team", "alt")),
        "batter_home_runs": (("home run", "homerun"), ("team", "first", "alt")),
        # Additional DFS-quoted pitcher markets (board slugs: outs-recorded,
        # hits-allowed, earned-runs-allowed, walks-allowed).
        "pitcher_outs": (("outs",), ("team", "alt")),
        "pitcher_hits_allowed": (("hits allowed",), ("team", "alt")),
        "pitcher_earned_runs": (("earned run",), ("team", "alt")),
        "pitcher_walks": (("walks allowed", "walks",), ("team", "alt", "intentional")),
    },
    "WNBA": {
        "Points": (("points",), ("team", "rebounds", "assists", "alt", "quarter", "half")),
        "Rebounds": (("rebounds",), ("team", "points", "assists", "alt")),
        "Assists": (("assists",), ("team", "points", "rebounds", "alt")),
        "3-Pointers Made": (("three", "3-point"), ("team", "alt", "attempt")),
    },
    "NBA": {
        "Points": (("points",), ("team", "rebounds", "assists", "alt", "quarter", "half")),
        "Rebounds": (("rebounds",), ("team", "points", "assists", "alt")),
        "Assists": (("assists",), ("team", "points", "rebounds", "alt")),
        "3-Pointers Made": (("three", "3-point"), ("team", "alt", "attempt")),
    },
}


def prop_market_ids(sport: str) -> dict[str, int]:
    """{our_market_name: live market id} resolved from /markets."""
    out: dict[str, int] = {}
    rules = _PROP_KEYWORDS.get(sport, {})
    for mid, info in sorted(market_lookup(sport).items()):
        text = (f"{info.get('name') or ''} {info.get('slug') or ''}"
                .lower().replace("-", " "))
        if not text.strip():
            continue
        for market, (need, block) in rules.items():
            if market in out:
                continue
            if any(k in text for k in need) and not any(b in text for b in block):
                out[market] = mid
    return out


def _match_market_name(sport: str, text: str) -> str | None:
    """Map a market's name/slug text to our prop-market name via the keywords."""
    text = (text or "").lower().replace("-", " ")
    for market, (need, block) in _PROP_KEYWORDS.get(sport, {}).items():
        if any(k in text for k in need) and not any(b in text for b in block):
            return market
    return None


def prop_offer_lines(sport: str, date: str) -> list[dict]:
    """Per-book prop offer rows (incl. PrizePicks/Underdog) driven off the live
    props board. The /markets ids resolved by ``prop_market_ids`` don't line up
    with the ids the offers feed serves, so we read the market_ids AND event_ids
    that actually appear on today's props and fetch /offers for those — each row
    tagged with our market name. Returns flattened rows (see ``flatten_offers``).
    """
    raw = props(sport, date)
    flat = flatten_props(raw)
    lookup = market_lookup(sport)
    # player_id -> name from the board, to backfill offer rows that carry only an
    # id (prop /offers identify the player by id, not always by name).
    id2name: dict[str, str] = {}
    for p in raw:
        pid = _dig(p, "participant.id", "participant.player.id", "player_id")
        nm = _dig(p, "participant.name", "participant.player.name", "player.name")
        if pid is not None and nm:
            id2name[str(pid)] = nm
    # market_id -> (our_name, [event_ids]) from what's actually on the board
    by_market: dict[int, dict] = {}
    for p in flat:
        mid = p.get("market_id")
        if mid is None:
            continue
        mid = int(mid)
        slot = by_market.setdefault(mid, {"events": set()})
        if "name" not in slot:
            info = lookup.get(mid, {})
            slot["name"] = _match_market_name(
                sport, f"{info.get('name','')} {info.get('slug','')}")
        ev = p.get("event_id")
        if ev is not None:
            slot["events"].add(ev)
    rows: list[dict] = []
    for mid, slot in by_market.items():
        name = slot.get("name")
        if not name:
            continue  # not a market we model
        evs = list(slot["events"])[:25]
        try:
            offers = _offers(sport, mid, evs)
        except Exception:
            continue
        for r in flatten_offers(offers):
            r["market"] = name
            if not r.get("participant") and r.get("player_id") is not None:
                r["participant"] = id2name.get(str(r["player_id"]))
            rows.append(r)
    filled = sum(1 for r in rows if r.get("participant"))
    log.info("prop_offer_lines: %d rows, %d named (board id2name=%d)",
             len(rows), filled, len(id2name))
    return rows


def _offers(sport, market_id, event_ids):
    """Thin offers() wrapper kept separate so prop_offer_lines can be mocked."""
    return offers(sport, market_id, event_ids or None)
