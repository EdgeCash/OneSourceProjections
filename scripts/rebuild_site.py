"""Rebuild data/output/latest.json from the committed snapshot library —
no BettingPros/FantasyPros credits burned. Free sources (MLB StatsAPI,
ESPN) still fetch live. Use after model/UI tweaks to refresh the site.

Deliberately does NOT re-archive projections or grade results: the
forward-test record stays anchored to what was projected at the time.

Usage:
    python scripts/rebuild_site.py [--dates 2026-06-12,2026-06-13]
"""

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from onesource import pipeline, replay, results  # noqa: E402
from onesource.config import OUTPUT_DIR  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("rebuild")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", default=None,
                    help="comma-separated; default: dates in current latest.json")
    args = ap.parse_args()

    if args.dates:
        dates = [d.strip() for d in args.dates.split(",")]
    else:
        cur = OUTPUT_DIR / "latest.json"
        dates = (json.loads(cur.read_text()).get("dates")
                 if cur.exists() else None) or []
    if not dates:
        sys.exit("no dates to rebuild — pass --dates")

    replay.activate()
    slates = {}
    for d in dates:
        replay.set_date(d)
        try:
            slates[d] = pipeline.run(d, write=False)["sports"]
        except Exception as e:
            log.error("rebuild %s failed: %s", d, e)
            slates[d] = {}

    out = {
        "generated_at": pd.Timestamp.now("UTC").isoformat(),
        "primary_date": dates[-1],
        "dates": dates,
        "slates": slates,
        "performance": results.performance(),
        "rebuilt_offline": True,
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "latest.json").write_text(json.dumps(out, indent=1, default=str))
    for d in dates:
        counts = {k: (len(v.get("games", [])), len(v.get("props", [])))
                  for k, v in slates[d].items()}
        print(f"{d}: {counts}")
    print("rebuilt latest.json from library (0 API credits)")


if __name__ == "__main__":
    main()
