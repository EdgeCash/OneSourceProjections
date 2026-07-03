"""Home-plate umpire scoring/strikeout tendencies -> a committed table the live
pipeline reads.

Umpires call meaningfully different strike zones: a tight zone yields more walks
and runs, a wide one more strikeouts and fewer runs. The effect per game is
small but real and persistent enough to nudge the strikeout/run environment.

The historical base is built from the **committed game-context backfill**
(``data/history/backfill/mlb/<season>/game_context.jsonl.gz``), which records the
home-plate ump as a **retrosheet biofile id** (``ump_home`` e.g. ``barkl901``)
plus each game's total runs, strikeouts, and batters faced. We aggregate per ump
id across every committed season, resolve the id to a name via the retrosheet
biofile (``reference/biofile.csv`` from chadwickbureau/retrosheet — the same
source the handedness map uses), and index each ump against the league:

  * runs_idx  — runs per game vs league (the scoring-environment tendency)
  * k_idx     — strikeouts per batter faced vs league (K-rate tendency; per-BF
                so it's not confounded by game length / extra innings)

Both indexes are shrunk toward 1.0 by the ump's game count so a light-workload
ump can't swing on a handful of games. The multi-season base is what makes the
table trustworthy enough to feed the model (config.UMPIRE_*), not just surface
as context.

``refresh_current_season`` tops the base up with the current season's finals from
the live StatsAPI boxscore (which the committed backfill doesn't cover yet),
matched to the same name-keyed rows so the daily hourly job keeps it fresh.

Writes: data/history/mlb_umpires.json
  {"generated_utc", "seasons": [...], "last_date",
   "league": {runs_pg, k_per_bf, games},
   "umpires": {name: {retro_id, games, runs_pg, k_per_bf,
                      runs_idx, k_idx, bb_idx, _runs, _ks, _bf}}}
The raw ``_runs/_ks/_bf`` sums are kept so the table can be extended in place.

Usage:
    python scripts/build_mlb_umpires.py                 # (re)build from context
    python scripts/build_mlb_umpires.py --refresh       # top up current season
    python scripts/build_mlb_umpires.py --biofile path/to/biofile.csv
"""
from __future__ import annotations

import argparse
import glob
import gzip
import json
import subprocess
import sys
import tempfile
from datetime import date as _date
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from project547.config import REPO_ROOT  # noqa: E402
from project547.names import normalize  # noqa: E402

BASE = "https://statsapi.mlb.com/api/v1"
OUT = REPO_ROOT / "data" / "history" / "mlb_umpires.json"
CONTEXT_GLOB = str(REPO_ROOT / "data" / "history" / "backfill" / "mlb" / "*"
                   / "game_context.jsonl.gz")
BIOFILE_REPO = "https://github.com/chadwickbureau/retrosheet.git"
SHRINK_GAMES = 100.0   # games of "league-average" prior; a 100-game ump is 1/2 shrunk


# ---------------------------------------------------------------------------
# retrosheet biofile: ump id -> display name (matches the live StatsAPI name)
# ---------------------------------------------------------------------------
def _fetch_biofile() -> str:
    tmp = Path(tempfile.mkdtemp()) / "retro"
    subprocess.run(["git", "clone", "--depth", "1", "--filter=blob:none",
                    "--no-checkout", BIOFILE_REPO, str(tmp)], check=True,
                   capture_output=True)
    subprocess.run(["git", "-C", str(tmp), "checkout", "HEAD", "--",
                    "reference/biofile.csv"], check=True, capture_output=True)
    return str(tmp / "reference" / "biofile.csv")


def _biofile_names(path: str | None) -> dict[str, str]:
    """{retrosheet_id: display_name} for everyone in the biofile who ever umpired
    (UMP.DEBUT set). The name is ``NICKNAME LAST`` — the common form StatsAPI's
    live officials block also uses (e.g. barkl901 -> 'Lance Barksdale')."""
    import csv
    if not path:
        path = _fetch_biofile()
    out: dict[str, str] = {}
    with open(path, newline="", encoding="latin-1") as fh:
        for r in csv.DictReader(fh):
            pid = (r.get("PLAYERID") or "").strip()
            if not pid or not (r.get("UMP.DEBUT") or "").strip():
                continue
            first = (r.get("NICKNAME") or r.get("FIRST") or "").strip()
            last = (r.get("LAST") or "").strip()
            if first and last:
                out[pid] = f"{first} {last}"
    return out


# ---------------------------------------------------------------------------
# aggregation + indexing
# ---------------------------------------------------------------------------
def _idx(rate: float, league: float, n: int) -> float:
    if league <= 0:
        return 1.0
    raw = rate / league
    w = n / (n + SHRINK_GAMES)          # sample-size shrinkage toward 1.0
    return round(1.0 + (raw - 1.0) * w, 4)


def _finalize(agg: dict, seasons: list, last_date: str | None) -> dict:
    """Compute league averages + shrunk indexes from raw per-ump sums. ``agg`` is
    ``{name: {retro_id, games, runs, ks, bf}}``."""
    tot = {"runs": 0, "ks": 0, "bf": 0, "games": 0}
    for a in agg.values():
        for k in ("runs", "ks", "bf", "games"):
            tot[k] += a[k]
    g = max(tot["games"], 1)
    lg_runs_pg = tot["runs"] / g
    lg_k_per_bf = tot["ks"] / max(tot["bf"], 1)

    umps = {}
    for name, a in agg.items():
        n = a["games"] or 1
        rpg = a["runs"] / n
        kbf = a["ks"] / max(a["bf"], 1)
        umps[name] = {
            "retro_id": a.get("retro_id"),
            "games": a["games"],
            "runs_pg": round(rpg, 3),
            "k_per_bf": round(kbf, 4),
            "runs_idx": _idx(rpg, lg_runs_pg, n),
            "k_idx": _idx(kbf, lg_k_per_bf, n),
            "bb_idx": None,   # walks aren't in the committed context
            "_runs": a["runs"], "_ks": a["ks"], "_bf": a["bf"],
        }
    return {
        "generated_utc": _date.today().isoformat(),
        "seasons": sorted(seasons),
        "last_date": last_date,
        "league": {"runs_pg": round(lg_runs_pg, 3),
                   "k_per_bf": round(lg_k_per_bf, 4),
                   "games": tot["games"]},
        "umpires": dict(sorted(umps.items())),
    }


def build_from_context(biofile: str | None = None) -> dict:
    """Aggregate every committed game_context row per home-plate ump, resolve the
    retrosheet id to a name via the biofile, and index against the league."""
    names = _biofile_names(biofile)
    print(f"biofile: {len(names)} umpire ids")
    agg: dict = {}
    seasons: set = set()
    last_date: str | None = None
    unmatched: set = set()
    for path in sorted(glob.glob(CONTEXT_GLOB)):
        with gzip.open(path, "rt") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("game_type") != "regular":
                    continue        # match the live top-up (regular season only)
                uid = row.get("ump_home")
                k = row.get("total_strikeouts")
                bf = row.get("total_batters_faced")
                if not uid or k is None or not bf:
                    continue
                runs = (row.get("visitor_runs") or 0) + (row.get("home_runs") or 0)
                name = names.get(uid)
                if not name:
                    unmatched.add(uid)
                    continue
                seasons.add(row.get("season"))
                d = row.get("date")
                if d and (last_date is None or d > last_date):
                    last_date = d
                a = agg.setdefault(name, {"retro_id": uid, "games": 0,
                                          "runs": 0, "ks": 0, "bf": 0})
                a["games"] += 1
                a["runs"] += runs
                a["ks"] += int(k)
                a["bf"] += int(bf)
    if unmatched:
        print(f"  {len(unmatched)} ump ids had no biofile name (skipped): "
              f"{sorted(unmatched)[:8]}...")
    # last_date is stored ISO so refresh can pick up cleanly
    iso = None
    if last_date and len(last_date) == 8:
        iso = f"{last_date[:4]}-{last_date[4:6]}-{last_date[6:]}"
    return _finalize(agg, sorted(s for s in seasons if s), iso)


# ---------------------------------------------------------------------------
# live current-season top-up (StatsAPI boxscores the backfill doesn't cover yet)
# ---------------------------------------------------------------------------
def _completed_game_pks(start: str, end: str, sess: requests.Session) -> list[int]:
    r = sess.get(f"{BASE}/schedule", params={
        "sportId": 1, "startDate": start, "endDate": end, "gameType": "R"},
        timeout=30)
    r.raise_for_status()
    pks = []
    for day in r.json().get("dates", []):
        for game in day.get("games", []):
            if game.get("status", {}).get("codedGameState") == "F":
                pks.append(game["gamePk"])
    return pks


def _hp_ump(box: dict) -> str | None:
    for o in box.get("officials", []) or []:
        if (o.get("officialType") or "").lower() == "home plate":
            return (o.get("official", {}) or {}).get("fullName")
    return None


def _game_totals(box: dict) -> tuple[int, int, int] | None:
    """(runs, strikeouts, batters_faced) for a finished game, or None if the
    boxscore is missing the team totals."""
    runs = ks = bf = 0
    for side in ("home", "away"):
        ts = box.get("teams", {}).get(side, {}).get("teamStats", {})
        bat, pit = ts.get("batting", {}), ts.get("pitching", {})
        if bat.get("runs") is None or pit.get("strikeOuts") is None \
                or pit.get("battersFaced") is None:
            return None
        runs += bat.get("runs") or 0
        ks += pit.get("strikeOuts") or 0
        bf += pit.get("battersFaced") or 0
    return runs, ks, bf


def _load_agg() -> tuple[dict, list, str | None]:
    """Existing per-ump raw sums + seasons + last date counted, for an in-place
    extend. Empty if there's no committed table yet."""
    if not OUT.exists():
        return {}, [], None
    try:
        tbl = json.loads(OUT.read_text())
    except Exception:
        return {}, [], None
    agg = {}
    for name, u in tbl.get("umpires", {}).items():
        agg[name] = {"retro_id": u.get("retro_id"), "games": u.get("games", 0),
                     "runs": u.get("_runs", 0), "ks": u.get("_ks", 0),
                     "bf": u.get("_bf", 0)}
    return agg, tbl.get("seasons", []), tbl.get("last_date")


def refresh_current_season(season: int) -> int:
    """Extend the committed table with the current season's finals played since it
    was last updated (or from the season start). Live top-up over the historical
    game-context base — cheap in steady state (only new days). Returns the ump
    count."""
    agg, seasons, last = _load_agg()
    if not agg:                          # no base yet -> build it first
        table = build_from_context()
        write(table)
        agg, seasons, last = _load_agg()
    start = _next_day(last) if last else f"{season}-03-01"
    end = _date.today().isoformat()
    if start > end:
        return len(agg)
    sess = requests.Session()
    pks = _completed_game_pks(start, end, sess)
    added = 0
    new_last = last
    for pk in pks:
        try:
            box = sess.get(f"{BASE}/game/{pk}/boxscore", timeout=30).json()
        except Exception:
            continue
        name = _hp_ump(box)
        totals = _game_totals(box)
        if not name or not totals:
            continue
        runs, ks, bf = totals
        a = agg.setdefault(name, {"retro_id": None, "games": 0,
                                  "runs": 0, "ks": 0, "bf": 0})
        a["games"] += 1
        a["runs"] += runs
        a["ks"] += ks
        a["bf"] += bf
        added += 1
    if season not in seasons:
        seasons = list(seasons) + [season]
    table = _finalize(agg, seasons, end if added else last)
    write(table)
    return len(table["umpires"])


def write(table: dict) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(table, separators=(",", ":"), sort_keys=True))


def _next_day(iso: str) -> str:
    from datetime import timedelta
    y, m, d = map(int, iso.split("-"))
    return (_date(y, m, d) + timedelta(days=1)).isoformat()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true",
                    help="top up the existing table with the current season's finals")
    ap.add_argument("--season", type=int, default=_date.today().year)
    ap.add_argument("--biofile", default=None,
                    help="path to reference/biofile.csv (else fetched from GitHub)")
    args = ap.parse_args()
    if args.refresh:
        n = refresh_current_season(args.season)
        print(f"Refreshed {OUT}: {n} umpires")
        return
    table = build_from_context(args.biofile)
    write(table)
    n = len(table["umpires"])
    print(f"Wrote {OUT}: {n} umpires over seasons {table['seasons']}, "
          f"league {table['league']}, {OUT.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
