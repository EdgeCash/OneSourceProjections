"""Run the pipeline and write data/output/latest.json.

Usage:
    python scripts/run_daily.py [date] [--sports MLB,WNBA,NHL]

With no --sports flag, runs every sport that is in season for the date
(see onesource/sports.py).
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from onesource import pipeline  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("date", nargs="?", default=None, help="YYYY-MM-DD")
    parser.add_argument("--sports", default=None,
                        help="comma-separated, e.g. MLB,WNBA (default: in-season)")
    args = parser.parse_args()

    sports = [s.strip().upper() for s in args.sports.split(",")] if args.sports else None
    out = pipeline.run(args.date, sports)
    for key, blob in out["sports"].items():
        status = blob.get("error", "ok")
        print(f"{key}: {len(blob['games'])} games, {len(blob['props'])} props ({status})")
