"""Run the full pipeline for a date and write data/output/latest.json.

Usage:
    python scripts/run_daily.py [YYYY-MM-DD]
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from onesource import pipeline  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

if __name__ == "__main__":
    date = sys.argv[1] if len(sys.argv) > 1 else None
    out = pipeline.run(date)
    print(f"OK: {len(out['games'])} games, {len(out['props'])} prop rows for {out['date']}")
