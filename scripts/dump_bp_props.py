#!/usr/bin/env python
"""Diagnose the BettingPros props response and the BOOK UNIVERSE it returns, to
confirm which book_id / name PrizePicks & Underdog use (so we can wire real DFS
lines). Fully shape-agnostic: it dumps whatever comes back (dict OR list) and
never slices a value blindly.

Run where BP_PARTNER_KEY + network exist (the GitHub Action does this), e.g.:
    python scripts/dump_bp_props.py MLB
It writes data/history/raw/bp_books_<sport>_<date>.json (committed by the Action)
and prints a summary to the log.
"""

import collections
import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from onesource import config                       # noqa: E402
from onesource.clients import bettingpros as bp    # noqa: E402


def _as_list(x):
    """Normalize any container into a list we can iterate/slice safely."""
    if isinstance(x, list):
        return x
    if isinstance(x, dict):
        return list(x.values())
    return [x]


def _types(seq):
    return dict(collections.Counter(type(x).__name__ for x in seq))


def _shape(x, depth=0, max_depth=3, max_items=4):
    """Compact, JSON-safe description of an arbitrary structure so we can see
    the real response shape in the committed artifact."""
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


def _fetch(sport, date, market_ids=None):
    """Mirror props() param building but return the FULL response so we can see
    every top-level key (props() drops everything but data['props'])."""
    params = {
        "sport": sport, "date": date, "location": "ALL",
        "limit": 500, "page": 1,
        "include_selections": "true", "include_markets": "false",
        "ev_threshold": "false",
    }
    # Only NFL/NBA support SGP correlations; requesting them elsewhere replaces
    # the props list with a warning string (mirror the client's gating).
    if sport.upper() in bp.CORRELATED_PICK_SPORTS:
        params["include_correlated_picks"] = "true"
        params["correlated_picks_limit"] = 6
    if market_ids:
        params["market_id"] = ":".join(str(m) for m in market_ids)
    data = bp._get("props", params)
    props = data.get("props", []) if isinstance(data, dict) else data
    return data, props


def _has_selections(seq):
    return any(isinstance(x, dict) and x.get("selections") for x in seq)


def main() -> None:
    sport = (sys.argv[1] if len(sys.argv) > 1 else "MLB").upper()
    date = sys.argv[2] if len(sys.argv) > 2 else datetime.date.today().isoformat()

    data, props = _fetch(sport, date)
    items = _as_list(props)
    top_keys = sorted(data.keys()) if isinstance(data, dict) else None
    print(f"{sport} {date}: top-level keys={top_keys}")
    print(f"  props is {type(props).__name__}; {len(items)} items, "
          f"types={_types(items)}")

    # The props board is sometimes empty/odd without explicit market ids — the
    # pipeline retries the same way, so mirror it here.
    retry_info = None
    if not _has_selections(items):
        try:
            ids = list(bp.prop_market_ids(sport).values())
        except Exception as e:
            ids = []
            print("prop_market_ids failed:", repr(e)[:200])
        if ids:
            data2, props2 = _fetch(sport, date, market_ids=ids)
            items2 = _as_list(props2)
            retry_info = {"n": len(items2), "types": _types(items2),
                          "market_ids": ids[:20]}
            print(f"  retry with {len(ids)} market ids -> {len(items2)} items, "
                  f"types={_types(items2)}")
            if _has_selections(items2):
                data, props, items = data2, props2, items2

    # Per-book lines (keeps book identity). prop_book_lines already guards
    # non-dict entries, so passing the normalized list is safe.
    rows = bp.prop_book_lines(items)
    counts = collections.Counter((r["book_id"], r["book_name"]) for r in rows)
    print(f"\n{len(rows)} per-book prop lines")
    if counts:
        print("books (book_id | name | #lines):")
        for (bid, name), n in sorted(counts.items(), key=lambda kv: -kv[1]):
            flag = "   <-- DFS" if name in bp.DFS_BOOK_NAMES else ""
            print(f"  {str(bid):>5} | {str(name):<18} | {n}{flag}")
    else:
        print("  (no per-book lines extracted -- see response_shape below)")

    dicts = [x for x in items if isinstance(x, dict)]
    summary = {
        "sport": sport, "date": date,
        "top_level_keys": top_keys,
        "props_container_type": type(props).__name__,
        "n_items": len(items), "item_types": _types(items),
        "retry_with_market_ids": retry_info,
        "first_prop_keys": sorted(dicts[0].keys()) if dicts else None,
        # full compact shape of the raw response -- this is what tells us the
        # real structure even when nothing flattens.
        "response_shape": _shape(data),
        "first_item_full": dicts[0] if dicts else (items[0] if items else None),
        "books": [{"book_id": bid, "book_name": name, "n_lines": n}
                  for (bid, name), n in sorted(counts.items(), key=lambda kv: -kv[1])],
        "dfs_sample": [{k: r.get(k) for k in ("participant", "side", "line",
                                              "odds", "book_id", "book_name")}
                       for r in bp.dfs_prop_lines(items)[:10]],
    }
    raw_dir = config.REPO_ROOT / "data" / "history" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    out = raw_dir / f"bp_books_{sport}_{date}.json"
    out.write_text(json.dumps(summary, indent=1, default=str))
    print(f"\nWrote diagnostic -> {out}")


if __name__ == "__main__":
    main()
