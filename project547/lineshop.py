"""Line shopping — best available price (and which book) per game/market/side
from the multi-book odds captured in the snapshot store. Beating the field on
price is the most reliable, lowest-effort edge in betting, so we surface where
to actually place each recommended bet.
"""

from __future__ import annotations

from collections import defaultdict

from .clv import _load_rows
from .names import normalize


def best_lines(sport: str, date: str, snap_dir=None) -> dict:
    """{frozenset({norm_home, norm_away}): {"moneyline": {norm_team: {price,
    book}}, "total": {"over"/"under": {price, book, line}}}} from the latest
    Odds API capture in the day's snapshot log."""
    rows = [r for r in _load_rows(sport, date, snap_dir)
            if r.get("kind") == "game" and r.get("source") == "oddsapi"]
    if not rows:
        return {}
    groups: dict = defaultdict(list)
    for r in rows:
        groups[r.get("event_id")].append(r)

    out: dict = {}
    for ers in groups.values():
        last = max((r.get("captured_at") or "") for r in ers)
        ers = [r for r in ers if (r.get("captured_at") or "") == last]
        teams = {normalize(r["participant"]) for r in ers
                 if r.get("market") == "moneyline" and r.get("participant")}
        if len(teams) != 2:
            continue
        rec: dict = {"moneyline": {}, "total": {}}
        for r in ers:
            if r.get("odds") is None:
                continue
            if r.get("market") == "moneyline" and r.get("participant"):
                t = normalize(r["participant"])
                cur = rec["moneyline"].get(t)
                if cur is None or r["odds"] > cur["price"]:
                    rec["moneyline"][t] = {"price": r["odds"], "book": r.get("book_id")}
            elif r.get("market") == "total":
                side = str(r.get("selection", "")).lower()
                key = "over" if "over" in side else "under" if "under" in side else None
                if key:
                    cur = rec["total"].get(key)
                    if cur is None or r["odds"] > cur["price"]:
                        rec["total"][key] = {"price": r["odds"], "book": r.get("book_id"),
                                             "line": r.get("line")}
        out[frozenset(teams)] = rec
    return out


def lookup(best: dict, home: str, away: str, market: str, sidekey: str) -> dict | None:
    """Best price for one bet. ``market`` is 'moneyline' or 'total';
    ``sidekey`` is a normalized team name (moneyline) or 'over'/'under'."""
    # prop rows carry no team context (NaN floats) — nothing to shop.
    if not (isinstance(home, str) and isinstance(away, str)
            and isinstance(market, str)):
        return None
    rec = best.get(frozenset({normalize(home), normalize(away)}))
    if not rec:
        return None
    return (rec.get(market) or {}).get(sidekey if market == "total" else normalize(sidekey))
