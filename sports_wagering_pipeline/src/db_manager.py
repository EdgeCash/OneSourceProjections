"""SQLite schema + local reads/writes for the pipeline cache.

Single persistent DB at ``data/pipeline.db``. Every read/write in the pipeline
goes through here so the cache-first token budget is enforced in one place.

ID normalization: a player is keyed by a deterministic ``Name + Team + Sport``
slug. The slug is stored in ``player_id_map`` and points at a stable
``master_player_id`` so FantasyPros projections and BettingPros prop lines land
on the same row even when the source strings differ slightly.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "pipeline.db"

CACHE_MINUTES = 30  # token-budget rule: a row younger than this is served from cache


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open (creating the file/dir if needed) the pipeline DB."""
    path = Path(db_path) if db_path else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create the four pipeline tables if they do not already exist."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS player_projections (
            master_player_id  TEXT PRIMARY KEY,
            player_name       TEXT,
            sport             TEXT,
            position          TEXT,
            projected_points  REAL,
            std_dev           REAL,
            salary_dk         INTEGER,
            last_updated      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS market_lines (
            line_id           TEXT PRIMARY KEY,
            master_player_id  TEXT,
            stat_type         TEXT,
            bookmaker         TEXT,
            line_value        REAL,
            over_odds         INTEGER,
            under_odds        INTEGER,
            -- Per-stat projection that pairs with THIS line's stat_type (e.g. a
            -- projected mean/std of hits for a "Hits" line). Real BettingPros
            -- lines are per stat, so the Pick'em edge is computed against the
            -- matching stat, not the DK fantasy-point aggregate. NULL for the
            -- sample source, which falls back to player_projections.
            proj_mean         REAL,
            proj_std          REAL,
            -- BettingPros "second opinion" for this line, JSON-encoded:
            -- {bp_projection, bp_ev, bp_recommended, open_over, open_under,
            --  public_pct_over}. Premium BP fields (auth=user); NULL otherwise.
            extra_json        TEXT,
            last_updated      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS player_id_map (
            norm_key          TEXT PRIMARY KEY,
            master_player_id  TEXT,
            last_updated      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS api_log (
            id            INTEGER PRIMARY KEY,
            timestamp     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            endpoint      TEXT,
            request_count INTEGER
        );

        CREATE INDEX IF NOT EXISTS idx_proj_sport   ON player_projections(sport);
        CREATE INDEX IF NOT EXISTS idx_lines_book   ON market_lines(bookmaker);
        CREATE INDEX IF NOT EXISTS idx_lines_master ON market_lines(master_player_id);
        """
    )
    conn.commit()


# --------------------------------------------------------------------------- #
# ID normalization
# --------------------------------------------------------------------------- #
def normalize_key(player_name: str, team: str, sport: str) -> str:
    """Deterministic Name+Team+Sport slug, e.g. ``mlb|nyy|aaronjudge``."""
    def slug(s: str) -> str:
        return re.sub(r"[^a-z0-9]", "", (s or "").lower())

    return f"{slug(sport)}|{slug(team)}|{slug(player_name)}"


def master_id(conn: sqlite3.Connection, player_name: str, team: str, sport: str) -> str:
    """Resolve (or register) the unified ``master_player_id`` for a player.

    The master id *is* the normalization key — deterministic and human-readable
    — with the mapping persisted so aliases can later be pointed at the same id.
    """
    key = normalize_key(player_name, team, sport)
    row = conn.execute(
        "SELECT master_player_id FROM player_id_map WHERE norm_key = ?", (key,)
    ).fetchone()
    if row:
        return row["master_player_id"]
    conn.execute(
        "INSERT INTO player_id_map (norm_key, master_player_id) VALUES (?, ?)",
        (key, key),
    )
    conn.commit()
    return key


# --------------------------------------------------------------------------- #
# Writes
# --------------------------------------------------------------------------- #
def upsert_projection(
    conn: sqlite3.Connection,
    master_player_id: str,
    player_name: str,
    sport: str,
    position: str,
    projected_points: float,
    std_dev: float,
    salary_dk: int,
) -> None:
    conn.execute(
        """
        INSERT INTO player_projections
            (master_player_id, player_name, sport, position,
             projected_points, std_dev, salary_dk, last_updated)
        VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(master_player_id) DO UPDATE SET
            player_name=excluded.player_name,
            sport=excluded.sport,
            position=excluded.position,
            projected_points=excluded.projected_points,
            std_dev=excluded.std_dev,
            salary_dk=excluded.salary_dk,
            last_updated=CURRENT_TIMESTAMP
        """,
        (master_player_id, player_name, sport, position,
         projected_points, std_dev, salary_dk),
    )
    conn.commit()


def upsert_market_line(
    conn: sqlite3.Connection,
    line_id: str,
    master_player_id: str,
    stat_type: str,
    bookmaker: str,
    line_value: float,
    over_odds: int = 0,
    under_odds: int = 0,
    proj_mean: float | None = None,
    proj_std: float | None = None,
    extra_json: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO market_lines
            (line_id, master_player_id, stat_type, bookmaker, line_value,
             over_odds, under_odds, proj_mean, proj_std, extra_json, last_updated)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(line_id) DO UPDATE SET
            master_player_id=excluded.master_player_id,
            stat_type=excluded.stat_type,
            bookmaker=excluded.bookmaker,
            line_value=excluded.line_value,
            over_odds=excluded.over_odds,
            under_odds=excluded.under_odds,
            proj_mean=excluded.proj_mean,
            proj_std=excluded.proj_std,
            extra_json=excluded.extra_json,
            last_updated=CURRENT_TIMESTAMP
        """,
        (line_id, master_player_id, stat_type, bookmaker, line_value,
         over_odds, under_odds, proj_mean, proj_std, extra_json),
    )
    conn.commit()


def log_api_call(conn: sqlite3.Connection, endpoint: str, request_count: int) -> None:
    conn.execute(
        "INSERT INTO api_log (timestamp, endpoint, request_count) "
        "VALUES (CURRENT_TIMESTAMP, ?, ?)",
        (endpoint, request_count),
    )
    conn.commit()


# --------------------------------------------------------------------------- #
# Reads / freshness
# --------------------------------------------------------------------------- #
def is_fresh(conn: sqlite3.Connection, table: str, where: str, params: tuple) -> bool:
    """True if the newest matching row is younger than ``CACHE_MINUTES``."""
    if table not in ("player_projections", "market_lines"):
        raise ValueError(f"unknown table: {table}")
    row = conn.execute(
        f"""
        SELECT (strftime('%s','now') - strftime('%s', MAX(last_updated)))
               AS age_seconds
        FROM {table} WHERE {where}
        """,
        params,
    ).fetchone()
    age = row["age_seconds"] if row else None
    return age is not None and age < CACHE_MINUTES * 60


def get_projections(conn: sqlite3.Connection, sport: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM player_projections WHERE sport = ? ORDER BY salary_dk DESC",
        (sport,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_market_lines(
    conn: sqlite3.Connection, sport: str, bookmaker: str
) -> list[dict]:
    """Lines for ``bookmaker`` joined to the player's projection (same sport)."""
    rows = conn.execute(
        """
        SELECT ml.*, pp.player_name, pp.sport, pp.position,
               pp.projected_points, pp.std_dev,
               -- Prefer the line's own per-stat projection (real BettingPros
               -- path); fall back to the player's fantasy-point projection
               -- (sample path, where the line is derived from it).
               COALESCE(ml.proj_mean, pp.projected_points) AS eff_mean,
               COALESCE(ml.proj_std,  pp.std_dev)          AS eff_std
        FROM market_lines ml
        JOIN player_projections pp
          ON pp.master_player_id = ml.master_player_id
        WHERE ml.bookmaker = ? AND pp.sport = ?
        """,
        (bookmaker, sport),
    ).fetchall()
    return [dict(r) for r in rows]


def api_usage_today(conn: sqlite3.Connection) -> int:
    """Total external requests logged in the last 24h (token-budget guard)."""
    row = conn.execute(
        """
        SELECT COALESCE(SUM(request_count), 0) AS n
        FROM api_log
        WHERE timestamp >= datetime('now', '-1 day')
        """
    ).fetchone()
    return int(row["n"]) if row else 0
