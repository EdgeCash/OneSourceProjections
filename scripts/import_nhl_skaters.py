"""Turn a big NHL skater box-score CSV into the compact per-game logs the app
already reads — so the 157 MB raw file never touches the repo, only the derived
~1-2 MB/season does (same pattern as the committed WNBA/NBA logs).

Usage (run wherever the file is reachable — your machine, CI, anywhere):

    python scripts/import_nhl_skaters.py nhl-skater-box-scores.csv.zip

pandas reads the .csv.zip directly (no unzip). The script auto-detects the
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
import sys
from pathlib import Path

import pandas as pd

OUT_ROOT = Path("data/history/backfill/nhl")

# canonical field -> ordered synonyms (lowercased, non-alnum stripped for match)
CANDIDATES = {
    "date":        ["gamedate", "date", "game_date"],
    "season":      ["season", "seasonyear", "year"],
    "player_id":   ["playerid", "player_id", "id"],
    "player_name": ["playername", "name", "player", "skater", "fullname"],
    "team":        ["team", "playerteam", "teamabbrev", "teamabbreviation"],
    "opponent":    ["opposingteam", "opponent", "opp", "opponentteam", "againstteam"],
    "home_away":   ["homeoraway", "home_or_away", "homeaway", "venue", "ishome", "home"],
    "position":    ["position", "pos", "playerposition"],
    "game_id":     ["gameid", "game_id", "gamepk", "gid"],
    # counting stats
    "goals":       ["goals", "g", "i_f_goals", "goalsfor"],
    "assists":     ["assists", "a", "i_f_assists", "totalassists"],
    "points":      ["points", "pts", "i_f_points"],
    "shots":       ["shots", "sog", "shotsongoal", "i_f_shotsongoal", "shotson goal"],
    "blocks":      ["blocks", "blockedshots", "blocked", "shotsblockedbyplayer"],
    "hits":        ["hits", "hitsfor", "i_f_hits"],
    "pim":         ["pim", "penaltyminutes", "penalty_minutes", "penalityminutes"],
    "toi":         ["timeonice", "toi", "icetime", "ice_time"],
}
REQUIRED = ("date", "player_name", "team", "goals", "assists", "shots")


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
    if "season" in m and pd.notna(row.get(m["season"])):
        s = int(str(row[m["season"]])[:4])
        return s
    d = str(row[m["date"]])
    # NHL season spans two years; Oct-Dec belongs to the season labelled by that
    # start year, Jan-Jun to the prior start year.
    y, mo = int(d[:4]), int(d[5:7]) if len(d) >= 7 else 7
    return y if mo >= 8 else y - 1


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
    src = sys.argv[1]
    print(f"Reading {src} ...")
    df = pd.read_csv(src)
    print(f"  {len(df):,} rows, {len(df.columns)} columns")
    m = _resolve(df.columns)
    print("\nColumn mapping detected:")
    for f in CANDIDATES:
        print(f"  {f:12} <- {m.get(f, '(none)')}")
    missing = [f for f in REQUIRED if f not in m]
    if missing:
        print(f"\nERROR: could not match required field(s): {missing}")
        print("CSV columns were:\n  " + "\n  ".join(map(str, df.columns)))
        print("\nAdd the right synonym to CANDIDATES and re-run.")
        sys.exit(2)

    by_season: dict[int, list[dict]] = {}
    for _, r in df.iterrows():
        try:
            season = _season_of(r, m)
        except Exception:
            continue
        def num(field):
            if field not in m:
                return None
            v = r.get(m[field])
            return None if pd.isna(v) else (int(v) if float(v).is_integer() else float(v))
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
            "position": r.get(m["position"]) if "position" in m else None,
            "goals": num("goals"), "assists": num("assists"),
            "points": num("points") if "points" in m else (
                (num("goals") or 0) + (num("assists") or 0)),
            "shots": num("shots"), "blocks": num("blocks"),
            "hits": num("hits"), "pim": num("pim"), "toi": num("toi"),
        }
        if not rec["player_name"] or rec["shots"] is None:
            continue
        by_season.setdefault(season, []).append(rec)

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
