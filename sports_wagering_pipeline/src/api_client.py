"""API ingestion with 30-minute cache enforcement + request logging.

Every ingestion function is cache-first: it asks ``db_manager.is_fresh`` before
spending an external request, honoring the 5,000 req/day token budget. Real
calls are logged to ``api_log``.

Live source: FantasyPros public API (projections) when ``FANTASYPROS_API_KEY``
is set. Pick'em books (PrizePicks / Underdog / DK Pick6) and offline runs fall
back to a deterministic sample slate so the whole pipeline is runnable end to
end for the A/B comparison without credentials.
"""

from __future__ import annotations

import os
import sqlite3

import requests

from . import db_manager

CACHE_MINUTES = db_manager.CACHE_MINUTES
DAILY_BUDGET = 5000
FANTASYPROS_BASE = "https://api.fantasypros.com/public/v2/json"


# --------------------------------------------------------------------------- #
# Projections (player_projections)
# --------------------------------------------------------------------------- #
def refresh_projections(
    conn: sqlite3.Connection, sport: str, date: str | None = None
) -> int:
    """Ensure fresh projections for ``sport`` exist; return row count touched.

    Serves from cache when the newest row for the sport is < 30 min old.
    """
    sport = sport.upper()
    if db_manager.is_fresh(conn, "player_projections", "sport = ?", (sport,)):
        return 0  # cache hit — no external request spent

    if db_manager.api_usage_today(conn) >= DAILY_BUDGET:
        raise RuntimeError("daily API budget (5000) exhausted; serving cache only")

    key = os.environ.get("FANTASYPROS_API_KEY")
    if key:
        records = _fetch_fantasypros(conn, sport, date, key)
        if records:
            _write_projections(conn, records)
            return len(records)

    # Offline / no-key / unsupported sport -> deterministic sample slate.
    records = _sample_projections(sport)
    _write_projections(conn, records)
    db_manager.log_api_call(conn, f"sample:projections:{sport}", 0)
    return len(records)


def _fetch_fantasypros(
    conn: sqlite3.Connection, sport: str, date: str | None, key: str
) -> list[dict]:
    """Pull daily projections from FantasyPros. Best-effort; [] on failure."""
    season = int((date or "2026-01-01")[:4]) if date else 2026
    path_map = {
        "MLB": f"mlb/{season}/projections",
        "NBA": f"nba/{season}/projections",
        "WNBA": f"wnba/{season}/projections",
    }
    path = path_map.get(sport)
    if not path:
        return []
    params = {"type": "daily"}
    if date:
        params["date"] = date
    try:
        resp = requests.get(
            f"{FANTASYPROS_BASE}/{path}",
            params=params,
            headers={"x-api-key": key, "Accept": "application/json"},
            timeout=30,
        )
        db_manager.log_api_call(conn, f"fantasypros:{path}", 1)
        resp.raise_for_status()
        raw = resp.json().get("player", [])
    except (requests.RequestException, ValueError):
        return []

    out = []
    for p in raw:
        name = p.get("name") or p.get("player_name")
        team = p.get("team") or p.get("team_id") or ""
        if not name:
            continue
        pts = _num(p.get("points") or p.get("fpts") or p.get("projected_points"))
        out.append(
            {
                "player_name": name,
                "team": team,
                "sport": sport,
                "position": (p.get("position") or p.get("position_id") or "UTIL"),
                "projected_points": pts,
                # FantasyPros gives means, not spreads; approximate a per-game
                # std at ~35% of the mean (documented heuristic, tune per stat).
                "std_dev": round(pts * 0.35, 2) if pts else 0.0,
                "salary_dk": int(_num(p.get("salary") or p.get("dk_salary")) or 0),
            }
        )
    return out


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
    conn: sqlite3.Connection, sport: str, platform: str = "PrizePicks"
) -> int:
    """Ensure fresh Pick'em lines for ``platform`` exist; return rows touched."""
    sport = sport.upper()
    if db_manager.is_fresh(
        conn, "market_lines", "bookmaker = ?", (platform,)
    ):
        return 0

    # Pick'em props require projections to exist first (id alignment).
    refresh_projections(conn, sport)
    lines = _sample_market_lines(conn, sport, platform)
    for ln in lines:
        db_manager.upsert_market_line(conn, **ln)
    db_manager.log_api_call(conn, f"sample:lines:{platform}:{sport}", 0)
    return len(lines)


# --------------------------------------------------------------------------- #
# Deterministic sample slates (offline demo / comparison harness)
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


def _sample_market_lines(
    conn: sqlite3.Connection, sport: str, platform: str
) -> list[dict]:
    """Derive Pick'em prop lines from the cached projections for the sport.

    Lines are offset from each player's projected points so both viable and
    non-viable plays appear — enough to exercise the ranking logic.
    """
    stat_for = {"MLB": "Fantasy Points", "WNBA": "Fantasy Points"}
    stat = stat_for.get(sport, "Fantasy Points")
    projections = db_manager.get_projections(conn, sport)

    lines = []
    for i, p in enumerate(projections):
        # Alternate the line above/below projection to seed both sides.
        offset = (0.6 if i % 2 == 0 else -0.6) * (p["std_dev"] or 1.0)
        line_value = round(p["projected_points"] + offset, 1)
        lines.append(
            {
                "line_id": f"{platform}:{p['master_player_id']}:{stat}",
                "master_player_id": p["master_player_id"],
                "stat_type": stat,
                "bookmaker": platform,
                "line_value": line_value,
                "over_odds": 0,   # flat Pick'em book
                "under_odds": 0,
            }
        )
    return lines


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0
