"""Manual / backfill driver for projection tracking.

The hourly job (scripts/hourly_update.py) does this automatically; use this to
backfill or run it by hand:

    PYTHONPATH=. python scripts/track.py [snapshot|grade|all] [YYYY-MM-DD]
"""
from __future__ import annotations

import sys
from datetime import date, timedelta

from project547 import tracking


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    anchor = sys.argv[2] if len(sys.argv) > 2 else date.today().isoformat()
    if cmd in ("snapshot", "all"):
        print("snapshot", anchor, "->", tracking.snapshot(anchor), "rows")
    if cmd in ("grade", "all"):
        for i in range(5):
            d = (date.fromisoformat(anchor) - timedelta(days=i)).isoformat()
            print("grade", d, "->", tracking.grade(d), "finals")
    print("\nsummary:", tracking.summary())


if __name__ == "__main__":
    main()
