"""Harvest player handedness (bats / throws) from the Chadwick Bureau retrosheet
biofile into a compact committed map — the one piece missing to wire the platoon
splits (which are already committed under data/history/backfill/mlb/*/splits).

MLB StatsAPI (the live source we'd normally hydrate handedness from) is network-
blocked in some sandboxes, but `chadwickbureau/retrosheet` is git-reachable, so
we pull its biofile and keep just recent players. Handedness is stable, so this
covers the current rosters (a brand-new debut we simply default to R/R until the
live feed fills it in).

Usage:
    python scripts/import_mlb_handedness.py [path/to/reference/biofile.csv]

If no path is given and the file isn't cached, it does a blob-less sparse clone
of chadwickbureau/retrosheet to fetch just reference/biofile.csv.

Writes: data/history/mlb_handedness.json  -> {norm_name: {"bats": "R|L|B",
"throws": "R|L"}}  (only players whose last game is >= MIN_YEAR).
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd

from project547.names import normalize

OUT = Path("data/history/mlb_handedness.json")
MIN_YEAR = 2015
REPO = "https://github.com/chadwickbureau/retrosheet.git"


def _fetch_biofile() -> str:
    tmp = Path(tempfile.mkdtemp()) / "retro"
    subprocess.run(["git", "clone", "--depth", "1", "--filter=blob:none",
                    "--no-checkout", REPO, str(tmp)], check=True,
                   capture_output=True)
    subprocess.run(["git", "-C", str(tmp), "checkout", "HEAD", "--",
                    "reference/biofile.csv"], check=True, capture_output=True)
    return str(tmp / "reference" / "biofile.csv")


def _last_year(v) -> int | None:
    # PLAY.LASTGAME is MM/DD/YYYY
    try:
        return int(str(v).strip()[-4:])
    except (ValueError, TypeError):
        return None


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else None
    if not path:
        print("Fetching biofile from chadwickbureau/retrosheet ...")
        path = _fetch_biofile()
    df = pd.read_csv(path, low_memory=False)
    df = df[df["BATS"].notna() | df["THROWS"].notna()].copy()
    df["yr"] = df["PLAY.LASTGAME"].map(_last_year)
    df = df[df["yr"].fillna(0) >= MIN_YEAR]
    df = df.sort_values("yr")   # most-recent last -> wins on name collisions
    out: dict[str, dict] = {}
    for _, r in df.iterrows():
        first = r.get("NICKNAME") or r.get("FIRST")
        last = r.get("LAST")
        if not isinstance(first, str) or not isinstance(last, str):
            continue
        name = normalize(f"{first} {last}")
        if not name:
            continue
        bats, throws = r.get("BATS"), r.get("THROWS")
        out[name] = {"bats": bats if isinstance(bats, str) else None,
                     "throws": throws if isinstance(throws, str) else None}
    kept = len(out)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, separators=(",", ":"), sort_keys=True))
    print(f"Wrote {OUT}: {kept} players (last game >= {MIN_YEAR}), "
          f"{OUT.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
