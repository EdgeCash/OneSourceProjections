"""Re-grade the historical results ledger's CLV under the current definitions.

The 2026-07 audit changed how CLV is computed (per-book same-line power de-vig,
line-matching so a bet is only scored against the close at its own line, push
handling). Rows graded before that carry old-definition CLV, so the displayed
Track Record mixes two definitions. This recomputes ONLY the CLV fields
(``clv``, ``close_line``, ``clv_line_moved``) for each graded bet row, replaying
the exact logic in results.grade_date against the retained snapshot store
(clv.closing_lines). Everything else on the row — won/push/pnl/ev/model_prob —
is preserved untouched, so no row is lost and the record stays intact.

A (sport, date) with no snapshot coverage is left exactly as-is (can't re-grade
without the close). Writes a timestamped backup of the ledger first. Idempotent:
running it twice produces the same ledger.

Usage:
    python scripts/regrade_clv.py --dry-run     # report what would change
    python scripts/regrade_clv.py               # apply (after a backup)
"""

import argparse
import json
import shutil
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from project547 import clv, results  # noqa: E402
from project547.names import normalize  # noqa: E402

CLV_MARKETS = {"moneyline", "total", "spread"}


def _teams(game: str) -> tuple[str, str]:
    """(away, home) from an 'AWAY @ HOME' label."""
    away, _, home = game.rpartition(" @ ")
    return away.strip(), home.strip()


def _recompute(row: dict, closes: dict) -> dict | None:
    """New {clv, close_line, clv_line_moved} for a bet row, or None to leave it
    unchanged (no closing coverage for its game). Mirrors results.grade_date."""
    away, home = _teams(row.get("game", ""))
    key = frozenset({normalize(home), normalize(away)})
    close = closes.get(key)
    if not close:
        return None
    price, side, line = row.get("price"), row.get("side"), row.get("line")
    mkt = row.get("market")

    if mkt == "moneyline":
        team = home if side == "home" else away
        fair = close.get("moneyline", {}).get(normalize(team))
        return {"clv": clv.clv_pct(price, fair)}

    if mkt == "total":
        tot = close.get("total", {})
        close_line = tot.get("line")
        same = (line is not None and close_line is not None
                and float(line) == float(close_line))
        return {"clv": clv.clv_pct(price, tot.get(side)) if same else None,
                "close_line": close_line,
                "clv_line_moved": (not same) if close_line is not None else None}

    if mkt == "spread":
        team = home if side == "home" else away
        cl = close.get("spread", {}).get(normalize(team))
        same = (cl is not None and cl.get("line") is not None and line is not None
                and float(cl["line"]) == float(line))
        return {"clv": clv.clv_pct(price, cl["prob"]) if same else None,
                "close_line": (cl or {}).get("line"),
                "clv_line_moved": (not same) if cl is not None else None}
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    ledger = results.load_ledger()
    bets = [r for r in ledger if r.get("market") in CLV_MARKETS]
    by_sd = defaultdict(list)
    for r in bets:
        by_sd[(r.get("sport"), r.get("date"))].append(r)

    closes_cache: dict = {}
    changed = skipped_dates = 0
    clv_before = [r["clv"] for r in bets if r.get("clv") is not None]

    for (sport, date), rows in by_sd.items():
        closes = closes_cache.get((sport, date))
        if closes is None:
            closes = closes_cache[(sport, date)] = clv.closing_lines(sport, date)
        if not closes:
            skipped_dates += 1
            continue
        for r in rows:
            new = _recompute(r, closes)
            if new is None:
                continue
            before = {"clv": r.get("clv"), "close_line": r.get("close_line"),
                      "clv_line_moved": r.get("clv_line_moved")}
            # apply: set clv always; set/clear close_line & clv_line_moved
            r["clv"] = new["clv"]
            for k in ("close_line", "clv_line_moved"):
                if k in new and new[k] is not None:
                    r[k] = new[k]
                elif k in r:
                    r.pop(k)
            after = {"clv": r.get("clv"), "close_line": r.get("close_line"),
                     "clv_line_moved": r.get("clv_line_moved")}
            if before != after:
                changed += 1

    clv_after = [r["clv"] for r in bets if r.get("clv") is not None]

    def _avg(xs):
        return round(100 * sum(xs) / len(xs), 3) if xs else None

    print(f"graded bet rows: {len(bets)}  |  (sport,date) groups: {len(by_sd)}  "
          f"|  no-snapshot groups skipped: {skipped_dates}")
    print(f"rows with changed CLV fields: {changed}")
    print(f"avg CLV%  before: {_avg(clv_before)} (n={len(clv_before)})  ->  "
          f"after: {_avg(clv_after)} (n={len(clv_after)})")

    if args.dry_run:
        print("\n[dry-run] no files written.")
        return
    if not changed:
        print("\nnothing to change.")
        return

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = results.LEDGER.with_suffix(f".jsonl.bak-{stamp}")
    shutil.copy2(results.LEDGER, backup)
    with results.LEDGER.open("w") as f:
        for r in ledger:
            f.write(json.dumps(r, default=str) + "\n")
    print(f"\nwrote {results.LEDGER} ({len(ledger)} rows); backup at {backup.name}")


if __name__ == "__main__":
    main()
