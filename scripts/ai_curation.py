"""Run the day's whole slate through Claude and cache the AI-curated plays.

Reads ``data/output/latest.json`` (baked hourly), curates the primary date's
slate (and the next date if present) with :mod:`project547.ai_curation`, and
writes ``data/output/ai_curation.json`` keyed by date. The static-site build
reads that file to show the per-card agreement badge and the AI-curated list;
if the file is absent or a date is missing the site simply renders without them.

This is deliberately decoupled from the hourly refresh — it runs a couple of
times a day via the ``ai-curation`` workflow (Opus 4.8, ~cents/day), so the
cost never scales with the hourly cadence. Needs ``ANTHROPIC_API_KEY``; exits 0
with a message (not an error) when the API isn't configured, so a missing key
never breaks CI.

Usage:  python scripts/ai_curation.py [--date YYYY-MM-DD] [--keep N]
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from project547 import ai_curation, config  # noqa: E402

CURATION_PATH = config.OUTPUT_DIR / "ai_curation.json"
MIN_EDGE = 0.02


def _load(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="curate only this slate date (YYYY-MM-DD)")
    ap.add_argument("--keep", type=int, default=7,
                    help="how many recent dates to retain in the cache")
    args = ap.parse_args()

    ready, reason = ai_curation.available()
    if not ready:
        print(f"ai_curation: skipping — {reason}")
        return 0

    latest = config.OUTPUT_DIR / "latest.json"
    if not latest.exists():
        print("ai_curation: no latest.json — run the pipeline first", file=sys.stderr)
        return 1
    data = json.loads(latest.read_text())
    slates = data.get("slates") or {}
    if not slates:
        print("ai_curation: no slates in latest.json")
        return 0

    if args.date:
        dates = [args.date]
    else:
        primary = data.get("primary_date")
        dates = [d for d in (primary, *sorted(slates.keys())) if d in slates]
        # de-dup, preserve order, cap at the two nearest live slates
        seen: list[str] = []
        for d in dates:
            if d not in seen:
                seen.append(d)
        dates = seen[:2]

    cache = _load(CURATION_PATH)
    stamp = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()
    for date_sel in dates:
        day = slates.get(date_sel) or {}
        if not day:
            continue
        try:
            out = ai_curation.curate(day, date_sel, min_edge=MIN_EDGE)
        except Exception as e:  # never let one bad slate kill the job
            print(f"ai_curation: {date_sel} failed — {e}", file=sys.stderr)
            continue
        out["generated_at"] = stamp
        out["date"] = date_sel
        cache[date_sel] = out
        print(f"ai_curation: {date_sel} — {len(out.get('top_plays', []))} plays, "
              f"{len(out.get('verdicts', {}))} verdicts over {out.get('game_count', 0)} games")

    # keep only the N most recent dates so the cache doesn't grow forever
    for old in sorted(cache.keys(), reverse=True)[args.keep:]:
        cache.pop(old, None)

    CURATION_PATH.write_text(json.dumps(cache, indent=2, allow_nan=False))
    print(f"ai_curation: wrote {CURATION_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
