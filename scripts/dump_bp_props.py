#!/usr/bin/env python
"""Discover which book_id / name PrizePicks & Underdog use in BettingPros (so we
can wire real DFS lines). Three angles, all dumped to one committed artifact:

  1. /books        -> the authoritative book_id -> name map (does PrizePicks /
                      Underdog appear, and under what id?)
  2. /props        -> the "best lines" board; which book ids actually show up on
                      live props (over.book / under.book / opening_line.book_id)
  3. /offers       -> the full per-book breakdown for one prop market
                      (selections[].books[].lines[]) -> are DFS books quoted here?

Fully shape-agnostic. Run where BP_PARTNER_KEY + network exist (the Action does):
    python scripts/dump_bp_props.py MLB
Writes data/history/raw/bp_books_<sport>_<date>.json and prints a summary.
"""

import collections
import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from onesource import config                       # noqa: E402
from onesource.clients import bettingpros as bp    # noqa: E402

DFS = bp.DFS_BOOK_NAMES


def _as_list(x):
    if isinstance(x, list):
        return x
    if isinstance(x, dict):
        return list(x.values())
    return [x]


def _types(seq):
    return dict(collections.Counter(type(x).__name__ for x in seq))


def _shape(x, depth=0, max_depth=4, max_items=4):
    if depth >= max_depth:
        return f"<{type(x).__name__}>"
    if isinstance(x, dict):
        out = {}
        for i, (k, v) in enumerate(x.items()):
            if i >= max_items:
                out["..."] = f"+{len(x) - max_items} more keys"
                break
            out[str(k)] = _shape(v, depth + 1, max_depth, max_items)
        return out
    if isinstance(x, list):
        head = [_shape(v, depth + 1, max_depth, max_items) for v in x[:max_items]]
        if len(x) > max_items:
            head.append(f"...+{len(x) - max_items} more")
        return head
    if isinstance(x, str):
        return x if len(x) <= 80 else x[:80] + "..."
    return x


def _is_dfs(name):
    return (name or "").strip().lower() in DFS


def fetch_props(sport, date):
    params = {
        "sport": sport, "date": date, "location": "ALL",
        "limit": 500, "page": 1,
        "include_selections": "true", "include_markets": "false",
        "ev_threshold": "false",
    }
    if sport.upper() in bp.CORRELATED_PICK_SPORTS:
        params["include_correlated_picks"] = "true"
        params["correlated_picks_limit"] = 6
    data = bp._get("props", params)
    props = data.get("props", []) if isinstance(data, dict) else data
    return _as_list(props)


def main() -> None:
    sport = (sys.argv[1] if len(sys.argv) > 1 else "MLB").upper()
    date = sys.argv[2] if len(sys.argv) > 2 else datetime.date.today().isoformat()

    # 1. The authoritative book directory ------------------------------------
    try:
        book_map = bp.book_lookup(sport)          # {id: name}
    except Exception as e:
        book_map = {}
        print("book_lookup failed:", repr(e)[:200])
    dfs_books = {bid: nm for bid, nm in book_map.items() if _is_dfs(nm)}
    print(f"/books: {len(book_map)} books for {sport}")
    for bid, nm in sorted(book_map.items()):
        print(f"  {bid:>4} | {nm}{'   <-- DFS' if _is_dfs(nm) else ''}")
    print(f"  -> DFS operators in directory: {dfs_books or 'NONE'}")

    # 2. Which book ids actually appear on the live props board ---------------
    props = fetch_props(sport, date)
    dicts = [p for p in props if isinstance(p, dict)]
    print(f"\n/props: {len(props)} items, types={_types(props)}")
    seen = collections.Counter()
    for p in dicts:
        for side in ("over", "under"):
            o = p.get(side) or {}
            if isinstance(o, dict):
                if o.get("book") is not None:
                    seen[int(o["book"])] += 1
                ol = (o.get("selection") or {}).get("opening_line") or {}
                if ol.get("book_id") is not None:
                    seen[int(ol["book_id"])] += 1
    props_books = [{"book_id": bid, "name": book_map.get(bid, "?"),
                    "appearances": n, "is_dfs": _is_dfs(book_map.get(bid))}
                   for bid, n in seen.most_common()]
    print("  book ids seen on props (id | name | count):")
    for r in props_books:
        print(f"    {r['book_id']:>4} | {r['name']:<18} | {r['appearances']}"
              f"{'   <-- DFS' if r['is_dfs'] else ''}")

    # 3. Full per-book breakdown for one prop market via /offers --------------
    offers_sample = None
    offers_books = []
    sample_market = dicts[0].get("market_id") if dicts else None
    if sample_market is not None:
        event_ids = [p.get("event_id") for p in dicts
                     if p.get("market_id") == sample_market and p.get("event_id")]
        try:
            raw_offers = bp.offers(sport, sample_market, event_ids=event_ids[:8] or None)
        except Exception as e:
            raw_offers = []
            print("offers failed:", repr(e)[:200])
        rows = bp.flatten_offers(raw_offers)
        cnt = collections.Counter()
        for r in rows:
            bid = r.get("book_id")
            cnt[bid] += 1
        offers_books = [{"book_id": bid, "name": book_map.get(bid, "?"),
                         "lines": n, "is_dfs": _is_dfs(book_map.get(bid))}
                        for bid, n in cnt.most_common()]
        offers_sample = {
            "market_id": sample_market, "n_offers": len(raw_offers),
            "n_flat_rows": len(rows),
            "first_offer_shape": _shape(raw_offers[0]) if raw_offers else None,
        }
        print(f"\n/offers market {sample_market}: {len(raw_offers)} offers, "
              f"{len(rows)} per-book rows")
        for r in offers_books:
            print(f"    {str(r['book_id']):>4} | {r['name']:<18} | {r['lines']}"
                  f"{'   <-- DFS' if r['is_dfs'] else ''}")

    summary = {
        "sport": sport, "date": date,
        "book_directory": {str(k): v for k, v in sorted(book_map.items())},
        "dfs_books_in_directory": {str(k): v for k, v in dfs_books.items()},
        "props_n_items": len(props),
        "books_seen_on_props": props_books,
        "props_first_item_shape": _shape(dicts[0]) if dicts else None,
        "offers": offers_sample,
        "books_seen_on_offers": offers_books,
    }
    raw_dir = config.REPO_ROOT / "data" / "history" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    out = raw_dir / f"bp_books_{sport}_{date}.json"
    out.write_text(json.dumps(summary, indent=1, default=str))
    print(f"\nWrote diagnostic -> {out}")


if __name__ == "__main__":
    main()
