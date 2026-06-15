#!/usr/bin/env python
"""Dump a BettingPros props sample and the BOOK UNIVERSE it returns, to confirm
which book_id / name PrizePicks & Underdog use in the props feed.

Run where BP_PARTNER_KEY + network exist (your prod / hourly box), e.g.:

    python scripts/dump_bp_props.py MLB
    python scripts/dump_bp_props.py NBA 2026-06-15

It prints one line per book ("id  name  #lines", DFS books flagged) — paste
that back and I can finish wiring the real PrizePicks/Underdog lines. It also
saves the first few raw props to data/history/raw/ in case the full shape helps.
"""

import collections
import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from onesource import config                       # noqa: E402
from onesource.clients import bettingpros as bp    # noqa: E402


def main() -> None:
    sport = (sys.argv[1] if len(sys.argv) > 1 else "MLB").upper()
    date = sys.argv[2] if len(sys.argv) > 2 else datetime.date.today().isoformat()

    raw = bp.props(sport, date)
    print(f"{sport} {date}: {len(raw)} props returned")
    if not raw:
        print("No props (off-season / no slate?) — try an in-season sport/date.")
        return

    rows = bp.prop_book_lines(raw)
    print(f"{len(rows)} per-book prop lines\n")
    print("books found  (book_id | book_name | #lines):")
    counts = collections.Counter((r["book_id"], r["book_name"]) for r in rows)
    for (bid, name), n in sorted(counts.items(), key=lambda kv: -kv[1]):
        flag = "   <-- DFS (matched)" if name in bp.DFS_BOOK_NAMES else ""
        print(f"  {str(bid):>5} | {str(name):<18} | {n}{flag}")

    dfs = bp.dfs_prop_lines(raw)
    print(f"\nDFS lines matched by current name set: {len(dfs)}")
    for r in dfs[:5]:
        print("   ", {k: r[k] for k in ("participant", "side", "line", "odds",
                                        "book_id", "book_name")})

    raw_dir = config.REPO_ROOT / "data" / "history" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    # machine-readable book universe (committed by the Action so it can be read
    # back without copy-pasting a phone log)
    summary = {
        "sport": sport, "date": date, "n_props": len(raw),
        "books": [{"book_id": bid, "book_name": name, "n_lines": n}
                  for (bid, name), n in sorted(counts.items(), key=lambda kv: -kv[1])],
        "dfs_sample": [{k: r[k] for k in ("participant", "side", "line", "odds",
                                          "book_id", "book_name")} for r in dfs[:10]],
    }
    (raw_dir / f"bp_books_{sport}_{date}.json").write_text(json.dumps(summary, indent=1))
    (raw_dir / f"bp_props_{sport}_{date}.json").write_text(
        json.dumps(raw[:5], indent=1, default=str))
    print(f"\nWrote book universe -> {raw_dir}/bp_books_{sport}_{date}.json")
    print(f"Wrote first 5 raw props -> {raw_dir}/bp_props_{sport}_{date}.json")


if __name__ == "__main__":
    main()
