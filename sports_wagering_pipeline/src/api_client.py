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

import json
import re as _re
import sqlite3

from . import db_manager

CACHE_MINUTES = db_manager.CACHE_MINUTES
DAILY_BUDGET = 5000

# DFS pick'em operators BettingPros carries, by book id. A tab per operator in
# the daily workbook. DraftKings Pick6 has no BP book id, so it stays derived.
DFS_OPERATORS = {
    "PrizePicks": 37, "Underdog": 36, "Betr": 45, "Sleeper": 63, "Dabble": 53,
}

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
    """Return (records, endpoint_label).

    ``sample`` returns the offline slate. ``shared`` returns real FantasyPros
    projections only (no sample fallback) — for sports FantasyPros doesn't cover
    (e.g. WNBA), projections come from BettingPros' own numbers, populated per
    line in ``_shared_market_lines``, so an empty FP result here is expected."""
    if source == "sample":
        return _sample_projections(sport), f"sample:projections:{sport}"
    records = _shared_projections(sport, date)
    return ((records, f"shared:fantasypros:{sport}") if records
            else ([], f"shared:none:proj:{sport}"))


def _fp_raw(sport: str, date: str | None) -> list[dict]:
    """Raw FantasyPros rows via project547's cached client.

    In the hourly job this is a warm-cache hit -> no external request. Returns []
    (never raises) when the cache is cold and no key is present, so callers can
    fall back to the sample slate.
    """
    try:
        from project547.clients import fantasypros as fp
    except Exception:
        return []

    season = int((date or "2026-01-01")[:4]) if date else 2026
    try:
        if sport == "MLB":
            return fp.mlb_projections(season, proj_type="daily", date=date)
        if sport == "NBA":
            return fp.nba_projections(season, date)
        if sport == "WNBA":
            get = getattr(fp, "wnba_projections", None)  # mirrors the nba endpoint
            return get(season, date) if get else []
    except Exception:
        # A cache miss with no key raises inside the client; treat as "no data".
        return []
    return []


def _shared_projections(sport: str, date: str | None) -> list[dict]:
    """Convert the shared FantasyPros rows into DK fantasy-point projections."""
    raw = _fp_raw(sport, date)
    if sport == "MLB":
        return [r for r in (_score_mlb(p) for p in raw) if r]
    return [r for r in (_score_hoops(p, sport) for p in raw) if r]


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

    ``shared`` uses real BettingPros lines only — if the book doesn't carry this
    sport today, the tab is honestly empty (no fabricated plays). ``sample``
    derives demo lines from the projection so the offline workbook is populated.
    """
    sport = sport.upper()
    if db_manager.is_fresh(conn, "market_lines", "bookmaker = ?", (platform,)):
        return 0

    refresh_projections(conn, sport, date=date, source=source)  # ids first

    if source == "shared":
        lines = _shared_market_lines(conn, sport, date, platform)
        endpoint = (f"shared:bettingpros:{platform}:{sport}" if lines
                    else f"shared:none:{platform}:{sport}")
    else:  # sample -> derived demo lines
        lines = _derive_market_lines(conn, sport, platform)
        endpoint = f"derived:lines:{platform}:{sport}"

    for ln in lines:
        db_manager.upsert_market_line(conn, **ln)
    db_manager.log_api_call(conn, endpoint, 0)
    return len(lines)


# BettingPros market name -> (display stat, mean-from-FP-row fn, std model key).
MLB_BATTER_STATS = {
    "batter_hits": (
        "Hits",
        lambda r: _num(r.get("hits")) or (
            _num(r.get("1b")) + _num(r.get("2b"))
            + _num(r.get("3b")) + _num(r.get("hrs"))),
        "count",
    ),
    "batter_home_runs": ("Home Runs", lambda r: _num(r.get("hrs")), "count"),
    "batter_total_bases": (
        "Total Bases",
        lambda r: (_num(r.get("1b")) + 2 * _num(r.get("2b"))
                   + 3 * _num(r.get("3b")) + 4 * _num(r.get("hrs"))),
        "tb",
    ),
}
MLB_PITCHER_STATS = {
    "pitcher_strikeouts": ("Strikeouts",
                           lambda r: _num(r.get("so") or r.get("k")), "count"),
    "pitcher_outs": ("Outs",
                     lambda r: _num(r.get("outs") or _num(r.get("ip")) * 3), "count"),
    "pitcher_hits_allowed": ("Hits Allowed", lambda r: _num(r.get("hits")), "count"),
    "pitcher_earned_runs": ("Earned Runs", lambda r: _num(r.get("er")), "count"),
    "pitcher_walks": ("Walks", lambda r: _num(r.get("bb")), "count"),
}
HOOPS_STATS = {
    "Points": ("Points", lambda r: _num(r.get("pts")), "hoops"),
    "Rebounds": ("Rebounds", lambda r: _num(r.get("reb")), "hoops"),
    "Assists": ("Assists", lambda r: _num(r.get("ast")), "hoops"),
    "3-Pointers Made": ("3-Pointers Made", lambda r: _num(r.get("3pm")), "hoops3"),
}


def _stat_std(mean: float, kind: str) -> float:
    """Per-stat spread model (documented heuristics; swap for a fitted spread)."""
    if kind == "count":   # Poisson-ish counting stat
        return (max(mean, 0.5)) ** 0.5
    if kind == "tb":      # total bases, overdispersed
        return (max(mean, 0.5) * 1.6) ** 0.5
    if kind == "hoops":   # points / reb / ast per game
        return max(mean * 0.30, 3.0)
    if kind == "hoops3":  # threes made
        return max((max(mean, 0.5)) ** 0.5, 0.9)
    return max(mean * 0.35, 1.0)


def _shared_market_lines(
    conn: sqlite3.Connection, sport: str, date: str | None, platform: str
) -> list[dict]:
    """Real PrizePicks/Underdog lines from the warm BettingPros cache, each
    paired with the matching per-stat FantasyPros projection.

    Reuses ``project547.clients.bettingpros`` with the same args the main engine
    used, so this is a warm-cache hit and issues no external request. Returns []
    (falling back to derived lines) when the cache is cold, the book is unknown,
    or no player matches a projection.
    """
    try:
        from project547.clients import bettingpros as bp
    except Exception:
        return []

    book_id = DFS_OPERATORS.get(platform)
    if book_id is None:
        return []  # e.g. DraftKings_Pick6 — no book id available; use derived

    try:
        offer_rows = bp.prop_offer_lines(sport, date)          # warm / cached
        dfs = bp.dfs_offer_lines(offer_rows, {book_id: platform.lower()})
    except Exception as e:
        # diagnostic: distinguish "BP errored" from "BP returned nothing"
        db_manager.log_api_call(
            conn, f"bpdiag:{sport}:{platform}:error={type(e).__name__}", 0)
        return []
    if not dfs:
        db_manager.log_api_call(
            conn, f"bpdiag:{sport}:{platform}:offers=0,rows={len(offer_rows or [])}", 0)
        return []

    hitters, pitchers, hoops = _fp_indices(sport, date)
    board = _bp_board_index(sport, date)   # BettingPros second opinion, by player+stat
    out = []
    for r in dfs:
        name, market = r.get("participant"), r.get("market")
        if not name or not market:
            continue
        spec, index = _resolve_stat(sport, market, hitters, pitchers, hoops)
        if not spec:
            continue
        label, mean_fn, kind = spec
        extra = board.get((_slug(name), market)) or {}
        rec = index.get(_slug(name))
        anchor = False
        if rec:                                   # FantasyPros projection (preferred)
            fp_row, team = rec
            mean = round(mean_fn(fp_row), 3)
        elif extra.get("bp_projection") is not None:  # BettingPros' own projection
            team = extra.get("player_team") or ""
            mean = round(_num(extra["bp_projection"]), 3)
            anchor = True
        else:
            continue  # no projection from FP or BP — skip, never fabricate
        line_value = r.get("over_line")
        if line_value is None:
            line_value = r.get("under_line")
        if line_value is None or mean <= 0:
            continue
        mid = db_manager.master_id(conn, name, team, sport)
        std = round(_stat_std(mean, kind), 3)
        if anchor:
            # No FP row exists for this sport (e.g. WNBA); anchor a projection
            # row so the market line joins and the player name resolves. The
            # per-stat mean lives on the line (proj_mean), so this row's
            # projected_points is just a join anchor.
            db_manager.upsert_projection(
                conn, master_player_id=mid, player_name=name, sport=sport,
                position="UTIL", projected_points=0.0, std_dev=0.0, salary_dk=0)
        out.append(
            {
                "line_id": f"{platform}:{mid}:{market}",
                "master_player_id": mid,
                "stat_type": label,
                "bookmaker": platform,
                "line_value": float(line_value),
                "over_odds": int(r.get("over_odds") or 0),
                "under_odds": int(r.get("under_odds") or 0),
                "proj_mean": mean,
                "proj_std": std,
                "extra_json": json.dumps(extra) if extra else None,
            }
        )
    db_manager.log_api_call(
        conn, f"bpdiag:{sport}:{platform}:offers={len(dfs)},matched={len(out)}", 0)
    return out


def _bp_board_index(sport: str, date: str | None) -> dict:
    """(player-slug, our-market) -> BettingPros second-opinion fields.

    Reads the BP props board (a call the main engine already warms) and keeps
    BP's own projection, EV, recommended side, opening prices, and public pick %
    — signal we already pay for. Returns {} (never raises) when unavailable.
    """
    try:
        from project547.clients import bettingpros as bp
    except Exception:
        return {}
    try:
        raw = bp.props(sport, date)
        flat = bp.flatten_props(raw)
        lookup = bp.market_lookup(sport)
    except Exception:
        return {}

    idx: dict = {}
    for r in flat:
        name, mid = r.get("participant"), r.get("market_id")
        if not name or mid is None:
            continue
        info = lookup.get(int(mid), {})
        market = bp._match_market_name(
            sport, f"{info.get('name', '')} {info.get('slug', '')}")
        if not market:
            continue
        idx[(_slug(name), market)] = {
            "player_team": r.get("player_team"),         # for sports FP misses
            "bp_line": r.get("bp_line"),                 # sharp consensus line
            "bp_projection": r.get("bp_projection"),
            "bp_ev": r.get("bp_ev"),
            "bp_probability": r.get("bp_probability"),   # P(recommended side)
            "bp_recommended": r.get("bp_recommended_side"),
            "bet_rating": r.get("bp_bet_rating"),        # BP 1-5 confidence
            # two-way price for de-vig (best, then consensus)
            "over_odds": r.get("over_odds") or r.get("over_consensus"),
            "under_odds": r.get("under_odds") or r.get("under_consensus"),
            "open_over": r.get("over_open"),
            "open_under": r.get("under_open"),
            "public_pct_over": r.get("pick_pct_over"),
            "form_l10": r.get("perf_l10"),               # recent over-rate
        }
    return idx


def _fp_indices(sport: str, date: str | None):
    """(hitters, pitchers, hoops) name-slug -> (fp_row, team) from shared FP."""
    hitters, pitchers, hoops = {}, {}, {}
    for p in _fp_raw(sport, date):
        name = p.get("name") or p.get("player_name")
        if not name:
            continue
        team = p.get("team_id") or p.get("team") or ""
        key = _slug(name)
        if sport == "MLB":
            is_pitcher = p.get("ip") is not None or (
                p.get("er") is not None and p.get("ab") is None
            )
            (pitchers if is_pitcher else hitters)[key] = (p, team)
        else:
            hoops[key] = (p, team)
    return hitters, pitchers, hoops


def _resolve_stat(sport: str, market: str, hitters, pitchers, hoops):
    """Map a BettingPros market to (stat spec, matching FP index)."""
    if sport == "MLB":
        if market in MLB_BATTER_STATS:
            return MLB_BATTER_STATS[market], hitters
        if market in MLB_PITCHER_STATS:
            return MLB_PITCHER_STATS[market], pitchers
        return None, None
    if market in HOOPS_STATS:
        return HOOPS_STATS[market], hoops
    return None, None


def _slug(s: str) -> str:
    return _re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _derive_market_lines(
    conn: sqlite3.Connection, sport: str, platform: str
) -> list[dict]:
    """Fallback lines: offset each player's fantasy-point projection to seed both
    viable and non-viable plays. Used for the sample source and when the shared
    BettingPros cache has no real lines. proj_mean/proj_std stay NULL so the
    engine falls back to the player's fantasy-point projection."""
    stat = "Fantasy Points"
    lines = []
    for i, p in enumerate(db_manager.get_projections(conn, sport)):
        offset = (0.6 if i % 2 == 0 else -0.6) * (p["std_dev"] or 1.0)
        lines.append(
            {
                "line_id": f"{platform}:{p['master_player_id']}:{stat}",
                "master_player_id": p["master_player_id"],
                "stat_type": stat,
                "bookmaker": platform,
                "line_value": round(p["projected_points"] + offset, 1),
                "over_odds": 0,
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
