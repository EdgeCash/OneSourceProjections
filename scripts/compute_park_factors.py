"""Derive empirical MLB park factors from the historical backfill and write
data/history/park_factors.json.

Park factor = (total runs/game in a team's home games) / (total runs/game
in that team's road games), shrunk toward 1.0 by sample size and
normalized so the league mean is 1.0. A value > 1 inflates scoring.

Usage:
    python scripts/compute_park_factors.py [--seasons 2022,2023,2024,2025]
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from onesource import history  # noqa: E402
from onesource.config import REPO_ROOT  # noqa: E402

SHRINK_GAMES = 200  # ghost games of neutral park pulling toward 1.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", default="2022,2023,2024,2025")
    args = ap.parse_args()
    seasons = [int(s) for s in args.seasons.split(",")]

    home_rs, home_g = defaultdict(float), defaultdict(int)
    road_rs, road_g = defaultdict(float), defaultdict(int)
    for y in seasons:
        bf = history.backfill_games("mlb", seasons=[y])
        for _, r in bf.iterrows():
            tot = r["home_score"] + r["away_score"]
            if pd.isna(tot):
                continue
            h, a = str(r["home_team"]), str(r["away_team"])
            if not h.isdigit():  # skip special-event games with numeric ids
                home_rs[h] += tot
                home_g[h] += 1
            if not a.isdigit():
                road_rs[a] += tot
                road_g[a] += 1

    raw = {}
    for t in home_g:
        if home_g[t] < 100 or road_g[t] < 100:
            continue
        hpg = home_rs[t] / home_g[t]
        rpg = road_rs[t] / road_g[t]
        w = home_g[t] / (home_g[t] + SHRINK_GAMES)
        raw[t] = w * (hpg / rpg) + (1 - w) * 1.0

    # normalize to league mean 1.0
    mean = sum(raw.values()) / len(raw)
    factors = {t: round(v / mean, 4) for t, v in raw.items()}

    out = {"seasons": seasons, "n_teams": len(factors),
           "method": "home/road runs-per-game, shrunk, league-normalized",
           "factors": dict(sorted(factors.items(), key=lambda kv: kv[1]))}
    path = REPO_ROOT / "data" / "history" / "park_factors.json"
    path.write_text(json.dumps(out, indent=1))
    print(f"wrote {path} ({len(factors)} parks); "
          f"range {min(factors.values())}-{max(factors.values())}")


if __name__ == "__main__":
    main()
