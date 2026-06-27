"""Build the daily editable wager workbook from data/output/latest.json.

Usage:
    python scripts/build_workbook.py            # uses data/output/latest.json
    python scripts/build_workbook.py --json path/to/slate.json --out my.xlsx

Writes data/output/workbook/latest.xlsx (+ a dated archive) by default. The
hourly job calls project547.workbook.build_to_disk directly; this script is the
manual/offline entry point.
"""

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from project547 import config, workbook  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", default=None,
                        help="slate JSON (default: data/output/latest.json)")
    parser.add_argument("--out", default=None,
                        help="output .xlsx (default: data/output/workbook/latest.xlsx)")
    args = parser.parse_args()

    src = Path(args.json) if args.json else config.OUTPUT_DIR / "latest.json"
    if not src.exists():
        sys.exit(f"no slate found at {src} — run scripts/run_daily.py first")
    data = json.loads(src.read_text())

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        workbook.build_workbook(data).save(out)
        path = out
    else:
        path = workbook.build_to_disk(data)
    print(f"OK: wrote {path} ({path.stat().st_size // 1024} KB)")
