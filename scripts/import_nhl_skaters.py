"""Turn a big NHL skater box-score CSV into the compact per-game logs the app
already reads — so the 157 MB raw file never touches the repo, only the derived
~1-2 MB/season does (same pattern as the committed WNBA/NBA logs).

Usage (run wherever the file is reachable — your machine, CI, anywhere):

    python scripts/import_nhl_skaters.py part1.zip [part2.zip ...]

Accepts one or more CSV / .zip files (they're concatenated). Only seasons >=
MIN_SEASON are written (older hockey is a different scoring era and just bloats
the repo; the live model only reads the last two seasons anyway) — override with
the env var NHL_MIN_SEASON. pandas reads .zip directly. The script auto-detects the
dataset's column names (they vary by source), prints the mapping it found, and
writes one gzip per season to:

    data/history/backfill/nhl/<year>/player_games.jsonl.gz

Then commit those small files and the NHL prop model lights up. If a required
column can't be matched, the script says which one and lists the CSV's columns
so we can add the synonym — nothing is guessed silently.

Output schema (one row per skater-game), matching playerlogs' expectations:
    game_id, date, season, team, opponent, is_home, player_id, player_name,
    position, goals, assists, points, shots, blocks, hits, pim, toi
"""
from __future__ import annotations

import gzip
import json
import os
import sys
from pathlib import Path

import pandas as pd

OUT_ROOT = Path("data/history/backfill/nhl")
MIN_SEASON = int(os.environ.get("NHL_MIN_SEASON", "2016"))

# canonical field -> ordered synonyms (lowercased, non-alnum stripped for match)
CANDIDATES = {
    "date":        ["gamedate", "date", "game_date"],
    "season":      ["season", "seasonyear", "year"],
    "player_id":   ["playerid", "player_id", "id"],
    "player_name": ["playername", "name", "player", "skater", "fullname"],
    "team":        ["team", "playerteam", "teamabbrev", "teamabbreviation"],
    "opponent":    ["opposingteam", "opponent", "opp", "opponentteam", "againstteam"],
    "home_away":   ["homeoraway", "home_or_away", "homeaway", "venue", "ishome", "home"],
    "position":    ["position", "pos", "playerposition", "positiongroup",
                    "position_group"],
    "game_id":     ["gameid", "game_id", "gamepk", "gid", "eventid", "event_id"],
    # counting stats
    "goals":       ["goals", "g", "i_f_goals", "goalsfor"],
    "assists":     ["assists", "a", "i_f_assists", "totalassists"],
    "points":      ["points", "pts", "i_f_points"],
    "shots":       ["shots", "sog", "shotsongoal", "i_f_shotsongoal", "shotson goal"],
    "blocks":      ["blocks", "blockedshots", "blocked", "shotsblockedbyplayer"],
    "hits":        ["hits", "hitsfor", "i_f_hits"],
    "pim":         ["pim", "penaltyminutes", "penalty_minutes", "penalityminutes"],
    "toi":         ["timeonice", "toi", "icetime", "ice_time"],
    # goalie stats (a goalie box-score file has these instead of g/a/shots)
    "saves":         ["sv", "saves"],
    "shots_against": ["sa", "shotsagainst", "shots_against"],
    "goals_against": ["ga", "goalsagainst", "goals_against"],
}
# a row is a skater if it has SOG, a goalie if it has saves; combined files
# (skaters + goalies concatenated) are supported — role is decided per row.
REQUIRED_ANY = ("shots", "saves")   # at least one must be present in the input


def _norm(c: str) -> str:
    return "".join(ch for ch in str(c).lower() if ch.isalnum())


def _resolve(cols) -> dict[str, str]:
    norm = {_norm(c): c for c in cols}
    found = {}
    for field, syns in CANDIDATES.items():
        for s in syns:
            if _norm(s) in norm:
                found[field] = norm[_norm(s)]
                break
    return found


def _season_of(row, m) -> int:
    # Always derive from the DATE using the start-year convention, to match the
    # committed backfill (data/history/backfill/nhl/2024 == the 2024-25 season).
    # The source CSV's own `season` column uses the *ending* year, so trusting it
    # would shift everything by one and break date/season joins.
    d = str(row[m["date"]])
    y, mo = int(d[:4]), int(d[5:7]) if len(d) >= 7 else 7
    return y if mo >= 8 else y - 1


def _toi_seconds(v):
    """'MM:SS' (or 'H:MM:SS') time-on-ice -> integer seconds; passes through
    plain numbers; None on anything unparseable."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip()
    if ":" in s:
        parts = [int(p) for p in s.split(":")]
        sec = 0
        for p in parts:
            sec = sec * 60 + p
        return sec
    try:
        return int(float(s))
    except ValueError:
        return None


def _is_home(row, m) -> bool | None:
    if "home_away" not in m:
        return None
    v = str(row[m["home_away"]]).strip().lower()
    if v in ("home", "h", "true", "1"):
        return True
    if v in ("away", "a", "false", "0", "road", "r"):
        return False
    return None


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    srcs = sys.argv[1:]
    frames = []
    for src in srcs:
        print(f"Reading {src} ...")
        d = pd.read_csv(src)
        print(f"  {len(d):,} rows, {len(d.columns)} columns")
        frames.append(d)
    df = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
    if len(frames) > 1:
        print(f"Combined: {len(df):,} rows")
    m = _resolve(df.columns)
    print("\nColumn mapping detected:")
    for f in CANDIDATES:
        print(f"  {f:12} <- {m.get(f, '(none)')}")
    if not any(f in m for f in REQUIRED_ANY):
        print(f"\nERROR: found neither skater shots nor goalie saves "
              f"({REQUIRED_ANY}). CSV columns were:\n  "
              + "\n  ".join(map(str, df.columns)))
        print("\nAdd the right synonym to CANDIDATES and re-run.")
        sys.exit(2)
    for f in ("date", "player_name"):
        if f not in m:
            print(f"\nERROR: required field {f!r} not found.")
            sys.exit(2)

    by_season: dict[int, list[dict]] = {}
    skipped = 0
    for _, r in df.iterrows():
        try:
            season = _season_of(r, m)
        except Exception:
            skipped += 1
            continue
        if season < MIN_SEASON:
            continue

        def num(field):
            if field not in m:
                return None
            v = r.get(m[field])
            if pd.isna(v):
                return None
            try:
                fv = float(v)
            except (TypeError, ValueError):
                return None
            return int(fv) if fv.is_integer() else fv

        goals, assists = num("goals"), num("assists")
        shots, saves = num("shots"), num("saves")
        is_goalie = saves is not None and shots is None
        position = r.get(m["position"]) if "position" in m and pd.notna(
            r.get(m["position"])) else ("G" if is_goalie else None)
        rec = {
            "game_id": str(r[m["game_id"]]) if "game_id" in m else None,
            "date": str(r[m["date"]])[:10],
            "season": season,
            "team": r.get(m["team"]) if "team" in m else None,
            "opponent": r.get(m["opponent"]) if "opponent" in m else None,
            "is_home": _is_home(r, m),
            "player_id": (int(r[m["player_id"]]) if "player_id" in m
                          and pd.notna(r[m["player_id"]]) else None),
            "player_name": r.get(m["player_name"]),
            "position": position,
            # skater stats (None for goalies)
            "goals": goals, "assists": assists,
            "points": (num("points") if "points" in m else
                       ((goals or 0) + (assists or 0))) if not is_goalie else None,
            "shots": shots, "blocks": num("blocks"),
            "hits": num("hits"), "pim": num("pim"),
            "toi": _toi_seconds(r.get(m["toi"])) if "toi" in m else None,
            # goalie stats (None for skaters)
            "saves": saves, "shots_against": num("shots_against"),
            "goals_against": num("goals_against"),
        }
        if not rec["player_name"] or (shots is None and saves is None):
            skipped += 1
            continue
        by_season.setdefault(season, []).append(rec)
    if skipped:
        print(f"  ({skipped:,} rows skipped: no name/shots or unparseable date)")

    print("\nWriting per-season logs:")
    for season, rows in sorted(by_season.items()):
        d = OUT_ROOT / str(season)
        d.mkdir(parents=True, exist_ok=True)
        path = d / "player_games.jsonl.gz"
        with gzip.open(path, "wt") as f:
            for rec in rows:
                f.write(json.dumps(rec, default=str) + "\n")
        print(f"  {season}: {len(rows):,} skater-games -> {path}")
    print("\nDone. Commit data/history/backfill/nhl/*/player_games.jsonl.gz "
          "(small) — NOT the raw csv.")


if __name__ == "__main__":
    main()
