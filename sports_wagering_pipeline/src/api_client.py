"""Ingestion with 30-min cache enforcement + request logging.

Two data sources, selected by ``source``:

* ``"shared"`` (default) -- consume the projections the main ``project547``
  engine has *already fetched*. We call the same
  ``project547.clients.fantasypros`` functions with the same arguments, so when
  this runs in the same hourly job it is a warm 1-hour-cache hit and issues
  **zero** external requests. The FantasyPros client only reads the API key on a
  cache *miss*, so running this step with no key at all makes a billable call
  structurally impossible -- it can only consume the cache. This is how both
  models run on one API-call schedule without double usage.
* ``"sample"`` -- a deterministic offline slate (positions + DK salaries baked
  in) so the whole pipeline, including the salary-cap DFS optimizer, is runnable
  with no credentials and no network.

FantasyPros MLB projections are per-game *stat lines* (hits, HR, RBI ...), so we
convert them to DraftKings fantasy points here. They carry no position or DK
salary, so the salary-cap DFS optimizer only runs on the ``"sample"`` source
until a real salary feed is wired in.
"""

from __future__ import annotations

import sqlite3

from . import db_manager

CACHE_MINUTES = db_manager.CACHE_MINUTES
DAILY_BUDGET = 5000

# DraftKings scoring. MLB hitter / pitcher and basketball (NBA/WNBA).
DK_MLB_HITTER = {"1b": 3, "2b": 5, "3b": 8, "hrs": 10, "rbi": 2,
                 "runs": 2, "bb": 2, "hbp": 2, "sb": 5}
DK_MLB_PITCHER = {"ip": 2.25, "so": 2, "w": 4, "er": -2,
                  "hits": -0.6, "bb": -0.6, "hbp": -0.6}
DK_HOOPS = {"pts": 1.0, "3pm": 0.5, "reb": 1.25, "ast": 1.5,
            "stl": 2.0, "blk": 2.0, "to": -0.5}


# --------------------------------------------------------------------------- #
# Projections (player_projections)
# --------------------------------------------------------------------------- #
def refresh_projections(
    conn: sqlite3.Connection,
    sport: str,
    date: str | None = None,
    source: str = "shared",
) -> int:
    """Ensure fresh projections for ``sport``; return rows touched.

    Serves from our own DB when the newest row is < 30 min old (token budget).
    """
    sport = sport.upper()
    if db_manager.is_fresh(conn, "player_projections", "sport = ?", (sport,)):
        return 0  # local cache hit — nothing fetched

    records, endpoint = _load_projections(conn, sport, date, source)
    _write_projections(conn, records)
    # request_count=0: shared mode never issues a billable request (warm cache /
    # keyless), and the sample source is offline. Real external spend is owned by
    # the main engine's hourly pull and logged there.
    db_manager.log_api_call(conn, endpoint, 0)
    return len(records)


def _load_projections(
    conn: sqlite3.Connection, sport: str, date: str | None, source: str
) -> tuple[list[dict], str]:
    """Return (records, endpoint_label). Falls back to sample when shared data
    is unavailable (project547 not importable, or an empty/offseason slate)."""
    if source == "sample":
        return _sample_projections(sport), f"sample:projections:{sport}"

    records = _shared_projections(sport, date)
    if records:
        return records, f"shared:fantasypros:{sport}"
    # Nothing in the shared cache (offseason, cold cache, or no project547).
    return _sample_projections(sport), f"sample:fallback:{sport}"


def _shared_projections(sport: str, date: str | None) -> list[dict]:
    """Read projections through project547's cached FantasyPros client.

    In the hourly job this is a warm-cache hit -> no external request. Returns
    [] (never raises) when the cache is cold and no key is present, so the
    caller can fall back to the sample slate.
    """
    try:
        from project547.clients import fantasypros as fp
    except Exception:
        return []

    season = int((date or "2026-01-01")[:4]) if date else 2026
    try:
        if sport == "MLB":
            raw = fp.mlb_projections(season, proj_type="daily", date=date)
            return [r for r in (_score_mlb(p) for p in raw) if r]
        if sport == "NBA":
            return [r for r in (_score_hoops(p, "NBA") for p in
                                fp.nba_projections(season, date)) if r]
        if sport == "WNBA":
            # wnba endpoint mirrors nba on the FantasyPros public API
            get = getattr(fp, "wnba_projections", None)
            raw = get(season, date) if get else []
            return [r for r in (_score_hoops(p, "WNBA") for p in raw) if r]
    except Exception:
        # A cache miss with no key raises inside the client; treat as "no data".
        return []
    return []


def _score_mlb(p: dict) -> dict | None:
    name = p.get("name") or p.get("player_name")
    if not name:
        return None
    team = p.get("team_id") or p.get("team") or ""
    is_pitcher = p.get("ip") is not None or (
        p.get("so") is not None and p.get("ab") is None
    )
    table = DK_MLB_PITCHER if is_pitcher else DK_MLB_HITTER
    pts = round(sum(w * _num(p.get(k)) for k, w in table.items()), 2)
    # FantasyPros carries no per-game std; approximate at ~40% of the mean
    # (documented heuristic — swap for a modeled spread when available).
    return {
        "player_name": name, "team": team, "sport": "MLB",
        "position": "P" if is_pitcher else "UTIL",
        "projected_points": pts, "std_dev": round(abs(pts) * 0.40, 2),
        "salary_dk": 0,  # FantasyPros supplies no DK salary
    }


def _score_hoops(p: dict, sport: str) -> dict | None:
    name = p.get("name") or p.get("player_name")
    if not name:
        return None
    team = p.get("team_id") or p.get("team") or ""
    if any(k in p for k in DK_HOOPS):
        pts = round(sum(w * _num(p.get(k)) for k, w in DK_HOOPS.items()), 2)
    else:
        pts = _num(p.get("points") or p.get("fpts"))
    return {
        "player_name": name, "team": team, "sport": sport,
        "position": (p.get("position") or "UTIL"),
        "projected_points": pts, "std_dev": round(abs(pts) * 0.25, 2),
        "salary_dk": int(_num(p.get("salary") or p.get("dk_salary"))),
    }


def _write_projections(conn: sqlite3.Connection, records: list[dict]) -> None:
    for r in records:
        mid = db_manager.master_id(conn, r["player_name"], r["team"], r["sport"])
        db_manager.upsert_projection(
            conn,
            master_player_id=mid,
            player_name=r["player_name"],
            sport=r["sport"],
            position=r["position"],
            projected_points=r["projected_points"],
            std_dev=r["std_dev"],
            salary_dk=r["salary_dk"],
        )


# --------------------------------------------------------------------------- #
# Market lines (market_lines)
# --------------------------------------------------------------------------- #
def refresh_market_lines(
    conn: sqlite3.Connection,
    sport: str,
    platform: str = "PrizePicks",
    source: str = "shared",
    date: str | None = None,
) -> int:
    """Ensure fresh Pick'em lines for ``platform``; return rows touched.

    Real book prop lines are not a stable committed artifact in this repo, so
    lines are derived from the (shared, real) projections as an over/under
    threshold. Wire a live Pick'em odds feed here to replace the derivation.
    """
    sport = sport.upper()
    if db_manager.is_fresh(conn, "market_lines", "bookmaker = ?", (platform,)):
        return 0

    refresh_projections(conn, sport, date=date, source=source)  # ids first
    lines = _derive_market_lines(conn, sport, platform)
    for ln in lines:
        db_manager.upsert_market_line(conn, **ln)
    db_manager.log_api_call(conn, f"derived:lines:{platform}:{sport}", 0)
    return len(lines)


def _derive_market_lines(
    conn: sqlite3.Connection, sport: str, platform: str
) -> list[dict]:
    """Offset each player's projection to seed both viable and non-viable
    over/under plays — exercises the ranking without a live odds feed."""
    stat = "Fantasy Points"
    projections = db_manager.get_projections(conn, sport)
    lines = []
    for i, p in enumerate(projections):
        offset = (0.6 if i % 2 == 0 else -0.6) * (p["std_dev"] or 1.0)
        lines.append(
            {
                "line_id": f"{platform}:{p['master_player_id']}:{stat}",
                "master_player_id": p["master_player_id"],
                "stat_type": stat,
                "bookmaker": platform,
                "line_value": round(p["projected_points"] + offset, 1),
                "over_odds": 0,   # flat Pick'em book
                "under_odds": 0,
            }
        )
    return lines


# --------------------------------------------------------------------------- #
# Deterministic sample slate (offline demo / DFS salary-cap input)
# --------------------------------------------------------------------------- #
def _sample_projections(sport: str) -> list[dict]:
    slates = {
        "MLB": [
            # name, team, pos, proj_pts, std, dk_salary
            ("Gerrit Cole", "NYY", "P", 22.5, 7.0, 8600),
            ("Tarik Skubal", "DET", "P", 24.1, 7.5, 9400),
            ("Zack Wheeler", "PHI", "P", 20.8, 6.8, 7600),
            ("Adley Rutschman", "BAL", "C", 9.6, 5.5, 4000),
            ("William Contreras", "MIL", "C", 9.1, 5.2, 3600),
            ("Freddie Freeman", "LAD", "1B", 10.4, 5.8, 4400),
            ("Vladimir Guerrero Jr.", "TOR", "1B", 10.9, 6.0, 4600),
            ("Mookie Betts", "LAD", "2B", 10.1, 5.7, 4500),
            ("Jose Altuve", "HOU", "2B", 9.3, 5.4, 3800),
            ("Jose Ramirez", "CLE", "3B", 10.7, 5.9, 4700),
            ("Rafael Devers", "BOS", "3B", 9.8, 5.6, 4100),
            ("Gunnar Henderson", "BAL", "SS", 10.5, 5.9, 4400),
            ("Bobby Witt Jr.", "KC", "SS", 11.2, 6.1, 4900),
            ("Aaron Judge", "NYY", "OF", 11.8, 6.3, 5200),
            ("Juan Soto", "NYY", "OF", 11.1, 6.0, 4800),
            ("Kyle Tucker", "HOU", "OF", 10.2, 5.7, 4300),
            ("Corbin Carroll", "ARI", "OF", 9.9, 5.6, 3600),
            ("Yordan Alvarez", "HOU", "OF", 10.8, 6.0, 4700),
        ],
        "WNBA": [
            ("A'ja Wilson", "LV", "F", 48.5, 11.0, 10200),
            ("Breanna Stewart", "NY", "F", 44.2, 10.5, 9400),
            ("Napheesa Collier", "MIN", "F", 43.1, 10.2, 9000),
            ("Alyssa Thomas", "CONN", "F", 41.8, 9.9, 8200),
            ("Angel Reese", "CHI", "F", 39.4, 9.6, 7400),
            ("Caitlin Clark", "IND", "G", 45.6, 10.8, 9600),
            ("Sabrina Ionescu", "NY", "G", 40.2, 9.7, 8400),
            ("Kelsey Plum", "LV", "G", 37.9, 9.2, 7800),
            ("Jackie Young", "LV", "G", 36.4, 9.0, 7200),
            ("Arike Ogunbowale", "DAL", "G", 38.8, 9.4, 8000),
        ],
    }
    rows = slates.get(sport, [])
    return [
        {
            "player_name": n, "team": t, "sport": sport, "position": pos,
            "projected_points": pts, "std_dev": std, "salary_dk": sal,
        }
        for (n, t, pos, pts, std, sal) in rows
    ]


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0
