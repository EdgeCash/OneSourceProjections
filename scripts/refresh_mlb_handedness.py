"""Live handedness refresh — fill the players the committed retrosheet map
misses (recent debuts) from MLB StatsAPI, so the platoon signal keeps working
for this season's rookies instead of silently no-op'ing on unknown hands.

The committed map (scripts/import_mlb_handedness.py, ~4k players) is harvested
from the retrosheet biofile and is stable, but a just-called-up hitter or a
pitcher making his debut won't be in it — and platoon.throws()/bats() then
return None, dropping the platoon nudge for that matchup. This walks the
upcoming slate(s), collects every probable pitcher and posted lineup batter,
and fills any whose handedness is unknown from the live people endpoint.

Usage:
    python scripts/refresh_mlb_handedness.py [--date YYYY-MM-DD ...]
"""
from __future__ import annotations

import argparse
import sys
from datetime import date as _date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from project547 import platoon  # noqa: E402
from project547.clients import mlb_statsapi  # noqa: E402


def slate_players(day: str) -> list[tuple[int, str]]:
    """Every (player_id, name) on a date's slate: both probable starters per
    game, plus posted lineup batters when the lineups are up."""
    pairs: list[tuple[int, str]] = []
    for g in mlb_statsapi.schedule(day):
        for side in ("home", "away"):
            pid, name = g.get(f"{side}_pitcher_id"), g.get(f"{side}_pitcher")
            if pid and name:
                pairs.append((pid, name))
        try:
            lineups = mlb_statsapi.batting_order(g["game_pk"])
        except Exception:
            lineups = {}
        for side in ("home", "away"):
            for entry in lineups.get(side, []):
                if entry.get("player_id") and entry.get("name"):
                    pairs.append((entry["player_id"], entry["name"]))
    return pairs


def refresh(dates: list[str]) -> int:
    pairs: list[tuple[int, str]] = []
    for d in dates:
        try:
            pairs += slate_players(d)
        except Exception as e:  # noqa: BLE001
            print(f"  slate {d} failed: {e}")
    # dedupe by id, preserving the first name seen
    seen: dict[int, str] = {}
    for pid, name in pairs:
        seen.setdefault(int(pid), name)
    added = platoon.refresh_handedness(seen.items())
    print(f"Checked {len(seen)} slate players across {dates}: "
          f"filled {added} previously-unknown handedness record(s).")
    return added


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", action="append", dest="dates",
                    help="date(s) to refresh (default: today + tomorrow, UTC)")
    args = ap.parse_args()
    if args.dates:
        dates = args.dates
    else:
        # UTC today/tomorrow — matches the hourly job's slate window; avoids the
        # sandbox-forbidden Date.now equivalents by using date.today() directly.
        t = _date.today()
        dates = [t.isoformat(), (t + timedelta(days=1)).isoformat()]
    refresh(dates)


if __name__ == "__main__":
    main()
