"""Hourly update: pull FantasyPros + BettingPros, snapshot odds, project the
upcoming slate(s), grade finished games, and rewrite the site's data file.

Run by .github/workflows/hourly.yml. After this commits data/output and the
snapshot/ledger files, the Streamlit app redeploys on push.

What it does each run:
  1. Snapshot current BettingPros odds for today+tomorrow (builds our
     own open/close history -> closing lines -> CLV going forward).
  2. Project today and tomorrow's slates (FantasyPros + BettingPros pulled
     inside the pipeline) and archive each for later grading.
  3. Grade games that finished (yesterday/today) into the results ledger.
  4. Write data/output/latest.json with both slates + the live performance
     summary.

Usage:
    python scripts/hourly_update.py [--date YYYY-MM-DD] [--no-snapshot]
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from onesource import pipeline, playerlogs, results, snapshots  # noqa: E402
from onesource.config import OUTPUT_DIR  # noqa: E402
from onesource.sports import active_sports, default_slate_date  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("hourly")

ET = ZoneInfo("America/New_York")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="anchor date (default: today ET)")
    ap.add_argument("--no-snapshot", action="store_true")
    args = ap.parse_args()

    today = (datetime.fromisoformat(args.date).date() if args.date
             else datetime.now(ET).date())
    tomorrow = today + timedelta(days=1)
    yesterday = today - timedelta(days=1)
    upcoming = [today.isoformat(), tomorrow.isoformat()]

    # 1) snapshot odds (closing-line history)
    if not args.no_snapshot:
        for d in upcoming:
            try:
                counts = snapshots.snapshot(d)
                log.info("snapshot %s: %s", d, counts)
            except Exception as e:
                log.error("snapshot %s failed: %s", d, e)

    try:
        n = snapshots.compact()
        if n:
            log.info("compacted %d old snapshot files", n)
    except Exception as e:
        log.error("snapshot compaction failed: %s", e)

    # 2) project upcoming slates and archive them
    slates = {}
    for d in upcoming:
        try:
            blob = pipeline.run(d, write=False)["sports"]
            slates[d] = blob
            results.archive_projections(d, blob)
        except Exception as e:
            log.error("projection %s failed: %s", d, e)
            slates[d] = {}

    # 3) grade finished games and ingest box scores. Grading sweeps a short
    #    window (idempotent) so missed runs or late-posting finals still get
    #    picked up; box-score ingest stays on yesterday+today (heavier).
    graded = results.grade_recent(today.isoformat(), days=4)
    ingested = 0
    for d in (yesterday.isoformat(), today.isoformat()):
        for sport in active_sports(d):
            try:
                ingested += playerlogs.ingest(sport, d)
            except Exception as e:
                log.error("box-score ingest %s %s failed: %s", sport, d, e)
    log.info("graded %d new rows, ingested %d player logs", graded, ingested)

    # 4) write the combined site data file
    perf = results.performance()
    primary = default_slate_date(upcoming, slates) or today.isoformat()
    from onesource.clients import oddsapi
    out = {
        "generated_at": datetime.now(ET).isoformat(),
        "primary_date": primary,
        "dates": upcoming,
        "slates": slates,
        "performance": perf,
        "odds_api_credits": oddsapi.credits_remaining(),
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "latest.json").write_text(json.dumps(out, indent=1, default=str))
    log.info("wrote latest.json | primary=%s | in-season=%s | perf=%s",
             primary, active_sports(primary), perf["overall"])
    print(f"OK: slates {upcoming}, graded {graded}, "
          f"record {perf['overall']}")


if __name__ == "__main__":
    main()
