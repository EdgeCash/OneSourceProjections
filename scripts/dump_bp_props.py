#!/usr/bin/env python
"""Diagnose the BettingPros props response and the BOOK UNIVERSE it returns, to
confirm which book_id / name PrizePicks & Underdog use (so we can wire real DFS
lines). Crash-proof: it dumps whatever shape comes back.

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


def _book_universe(raw):
    rows = bp.prop_book_lines(raw)
    counts = collections.Counter((r["book_id"], r["book_name"]) for r in rows)
    return rows, counts


def _types(seq):
    return dict(collections.Counter(type(x).__name__ for x in seq))


def main() -> None:
    sport = (sys.argv[1] if len(sys.argv) > 1 else "MLB").upper()
    date = sys.argv[2] if len(sys.argv) > 2 else datetime.date.today().isoformat()

    raw = bp.props(sport, date)
    print(f"{sport} {date}: {len(raw)} items, types={_types(raw)}")

    # the props board is sometimes empty/odd without explicit market ids — the
    # pipeline retries the same way, so mirror it here.
    retry_info = None
    if not any(isinstance(x, dict) and x.get("selections") for x in raw):
        try:
            ids = list(bp.prop_market_ids(sport).values())
        except Exception as e:
            ids = []
            print("prop_market_ids failed:", repr(e)[:200])
        if ids:
            raw2 = bp.props(sport, date, market_ids=ids)
            retry_info = {"n": len(raw2), "types": _types(raw2),
                          "market_ids": ids[:20]}
            print(f"retry with {len(ids)} market ids -> {len(raw2)} items, "
                  f"types={_types(raw2)}")
            if any(isinstance(x, dict) and x.get("selections") for x in raw2):
                raw = raw2

    rows, counts = _book_universe(raw)
    print(f"\n{len(rows)} per-book prop lines")
    if counts:
        print("books (book_id | name | #lines):")
        for (bid, name), n in sorted(counts.items(), key=lambda kv: -kv[1]):
            flag = "   <-- DFS" if name in bp.DFS_BOOK_NAMES else ""
            print(f"  {str(bid):>5} | {str(name):<18} | {n}{flag}")

    # everything I need to diagnose, committed for me to read back
    first_dicts = [x for x in raw if isinstance(x, dict)][:3]
    summary = {
        "sport": sport, "date": date,
        "n_items": len(raw), "item_types": _types(raw),
        "retry_with_market_ids": retry_info,
        "first_prop_keys": sorted(first_dicts[0].keys()) if first_dicts else None,
        "first_items": raw[:3] if not first_dicts else first_dicts[:1],
        "books": [{"book_id": bid, "book_name": name, "n_lines": n}
                  for (bid, name), n in sorted(counts.items(), key=lambda kv: -kv[1])],
        "dfs_sample": [{k: r[k] for k in ("participant", "side", "line", "odds",
                                          "book_id", "book_name")}
                       for r in bp.dfs_prop_lines(raw)[:10]],
    }
    raw_dir = config.REPO_ROOT / "data" / "history" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    out = raw_dir / f"bp_books_{sport}_{date}.json"
    out.write_text(json.dumps(summary, indent=1, default=str))
    print(f"\nWrote diagnostic -> {out}")


if __name__ == "__main__":
    main()
