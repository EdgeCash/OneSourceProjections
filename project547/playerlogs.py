"""Player game-log access for prop research: hit-rate splits
(L5/L10/L20/season/H2H vs a line) and the recent per-game series for the
prop bar chart.

Source: the committed box-score logs (data/history/backfill/<sport>/<year>/
player_games.jsonl, MLB 2024+, WNBA 2018+) plus an optional forward store
(data/history/playerlogs/<sport>.jsonl) that the hourly job appends to keep
the current season fresh. Everything here works offline from the imported
data; live freshness comes from the forward store.
"""

from __future__ import annotations

import json
from functools import lru_cache

import pandas as pd

from . import history
from .config import REPO_ROOT
from .names import normalize

FORWARD_DIR = REPO_ROOT / "data" / "history" / "playerlogs"
MIN_PLAY_MINUTES = 5   # basketball: drop DNP / garbage-time cameos from the logs

# prop market -> (box-score stat key, role filter or None)
MARKET_STAT = {
    # MLB (nested 'stats' dict)
    "pitcher_strikeouts": ("strikeOuts", "P"),
    "pitcher_outs": ("outs", "P"),
    "pitcher_hits_allowed": ("hits", "P"),       # pitcher row's hits = hits allowed
    "pitcher_earned_runs": ("earnedRuns", "P"),
    "pitcher_walks": ("baseOnBalls", "P"),       # pitcher row's BB = walks allowed
    "batter_hits": ("hits", "B"),
    "batter_total_bases": ("totalBases", "B"),
    "batter_home_runs": ("homeRuns", "B"),
    # WNBA / basketball (flat columns); keyed by normalized market name
    "points": ("points", None),
    "rebounds": ("rebounds", None),
    "assists": ("assists", None),
    "threes": ("three_made", None),
    "3-pointers made": ("three_made", None),
    "made threes": ("three_made", None),
    "steals": ("steals", None),
    "blocks": ("blocks", None),
    "pts+reb+ast": ("pra", None),
    "pra": ("pra", None),
    # NHL skater (flat columns; assists/points/blocks share the basketball keys
    # above since the column names match — only the hockey-specific ones here).
    "shots on goal": ("shots", None),
    "shots": ("shots", None),
    "sog": ("shots", None),
    "goals": ("goals", None),
    "blocked shots": ("blocks", None),
    "saves": ("saves", None),
    "goalie saves": ("saves", None),
    # Football — flat columns matching the committed backfill schema
    # (data/history/backfill/<nfl|ncaaf>/<year>/player_games.jsonl.gz).
    "passing yards": ("pass_yards", None),
    "rushing yards": ("rush_yards", None),
    "receiving yards": ("rec_yards", None),
    "rushing + receiving yards": ("scrimmage_yards", None),
    "rush + rec yards": ("scrimmage_yards", None),
    "receptions": ("receptions", None),
    "passing touchdowns": ("pass_touchdowns", None),
    "rushing touchdowns": ("rush_touchdowns", None),
    "receiving touchdowns": ("rec_touchdowns", None),
    "interceptions": ("interceptions", None),
    "completions": ("pass_completions", None),
    "pass completions": ("pass_completions", None),
    "pass attempts": ("pass_attempts", None),
    "passing attempts": ("pass_attempts", None),
    "carries": ("carries", None),
    "rushing attempts": ("carries", None),
    "anytime touchdown": ("scrim_tds", None),
    "anytime td": ("scrim_tds", None),
    "field goals made": ("field_goals_made", None),
    "kicking points": ("kicking_points", None),
    "longest reception": ("long_reception", None),
    "longest rush": ("long_rush", None),
}


def market_to_stat(market: str) -> tuple[str, str | None] | None:
    if not market:
        return None
    if market in MARKET_STAT:
        return MARKET_STAT[market]
    return MARKET_STAT.get(normalize(market))


@lru_cache(maxsize=8)
def _logs(sport: str, seasons: tuple[int, ...]) -> pd.DataFrame:
    """Long-form logs for the given seasons: one row per player-game with
    normalized name, date, season, opponent, and the stat columns we use."""
    sk = sport.lower()
    frames = []
    df = history.player_games(sk, seasons=list(seasons))
    if not df.empty:
        frames.append(_normalize_frame(sk, df))
    fwd = FORWARD_DIR / f"{sk}.jsonl"
    if fwd.exists():
        raw = pd.read_json(fwd, lines=True)
        raw = raw[raw["season"].isin(seasons)] if "season" in raw.columns else raw
        if not raw.empty:
            frames.append(_normalize_frame(sk, raw))
    if not frames:
        return pd.DataFrame(columns=["norm", "date", "season", "opp"])
    out = pd.concat(frames, ignore_index=True)
    # Drop non-appearances so a DNP isn't counted as "scored 0" — that biases
    # every rate/hit-rate low and creates a train/serve skew vs the prop-model
    # validators, which exclude them. Sport-agnostic: only filters on columns
    # that exist (basketball has did_not_play/minutes; MLB/NHL don't, untouched).
    if "did_not_play" in out.columns:
        out = out[~(out["did_not_play"] == True)]  # noqa: E712
    if "minutes" in out.columns:
        out = out[out["minutes"].isna() | (out["minutes"] >= MIN_PLAY_MINUTES)]
    out["date"] = pd.to_datetime(out["date"])
    out = out.sort_values("date").drop_duplicates(["norm", "date"], keep="last")
    return out


def _normalize_frame(sport: str, df: pd.DataFrame) -> pd.DataFrame:
    name_col = "player_name" if "player_name" in df.columns else "name"
    base = pd.DataFrame({
        "norm": df[name_col].map(normalize),
        "date": df["date"],
        "opp": df.get("opponent"),
        "role": df.get("role"),
    })
    if "season" in df.columns:
        base["season"] = df["season"]
    else:
        base["season"] = pd.to_datetime(df["date"]).dt.year
    # appearance flags (basketball) so _logs can drop non-appearances
    for flag in ("minutes", "did_not_play"):
        if flag in df.columns:
            base[flag] = df[flag]
    for key in ("strikeOuts", "hits", "totalBases", "homeRuns",
                "baseOnBalls", "earnedRuns", "inningsPitched"):
        if "stats" in df.columns:          # backfill: nested stats dict
            base[key] = df["stats"].map(lambda s, k=key: (s or {}).get(k))
        elif key in df.columns:            # forward store: flat columns
            base[key] = df[key]
    # Pitcher outs recorded = innings pitched × 3 (IP already stored as decimal
    # innings, e.g. 5⅔ -> 5.667), so the outs prop market can be graded.
    if "inningsPitched" in base.columns:
        base["outs"] = (pd.to_numeric(base["inningsPitched"], errors="coerce")
                        * 3).round()
    for col in ("points", "rebounds", "assists", "three_made", "steals",
                "blocks", "pra",
                "shots", "goals", "hits", "pim",    # NHL skater stats
                "saves", "shots_against", "goals_against"):  # NHL goalie stats
        if col in df.columns:
            base[col] = df[col]
    # Football flat columns (backfill + forward store share these names)
    for col in ("pass_yards", "rush_yards", "rec_yards", "scrimmage_yards",
                "receptions", "pass_touchdowns", "rush_touchdowns",
                "rec_touchdowns", "interceptions", "pass_completions",
                "pass_attempts", "carries", "scrim_tds", "field_goals_made",
                "kicking_points", "long_reception", "long_rush"):
        if col in df.columns:
            base[col] = df[col]
    # Anytime-TD: a player's scrimmage TDs (backfill stores rush/rec TDs apart)
    if "scrim_tds" not in base.columns and \
            {"rush_touchdowns", "rec_touchdowns"} <= set(df.columns):
        base["scrim_tds"] = (pd.to_numeric(df["rush_touchdowns"], errors="coerce").fillna(0)
                             + pd.to_numeric(df["rec_touchdowns"], errors="coerce").fillna(0))
    return base


def hit_rates(sport: str, player: str, market: str, line: float,
              opponent: str | None = None, season: int | None = None) -> dict:
    """Over-rate for the player's stat vs `line` across recent windows.
    Returns {l5, l10, l20, season, h2h} as fractions (or None if no data)."""
    info = market_to_stat(market)
    if info is None or line is None or pd.isna(line):
        return {}
    stat, _role = info
    seasons = tuple(sorted({season or pd.Timestamp.now().year,
                            (season or pd.Timestamp.now().year) - 1}))
    df = _logs(sport, seasons)
    if df.empty or stat not in df.columns:
        return {}
    g = df[(df["norm"] == normalize(player)) & df[stat].notna()].sort_values(
        "date", ascending=False)
    if g.empty:
        return {}
    over = (g[stat] > line)

    def rate(mask_series):
        n = len(mask_series)
        return round(float(mask_series.mean()), 3) if n else None

    out = {
        "l5": rate(over.head(5)),
        "l10": rate(over.head(10)),
        "l20": rate(over.head(20)),
        "n_l5": int(min(5, len(g))),
    }
    cur = season or g["season"].max()
    out["season"] = rate(over[g["season"] == cur])
    if opponent:
        out["h2h"] = rate(over[g["opp"].map(normalize) == normalize(opponent)])
    return out


def recent_series(sport: str, player: str, market: str, n: int = 12,
                  season: int | None = None) -> list[dict]:
    """Most recent games for the prop bar chart: [{date, value, opp}], oldest
    first so it plots left-to-right."""
    info = market_to_stat(market)
    if info is None:
        return []
    stat, _ = info
    seasons = tuple(sorted({season or pd.Timestamp.now().year,
                            (season or pd.Timestamp.now().year) - 1}))
    df = _logs(sport, seasons)
    if df.empty or stat not in df.columns:
        return []
    g = df[(df["norm"] == normalize(player)) & df[stat].notna()].sort_values(
        "date", ascending=False).head(n)
    g = g.iloc[::-1]
    return [{"date": d.strftime("%-m/%-d"), "value": float(v),
             "opp": (o if isinstance(o, str) else "")}
            for d, v, o in zip(g["date"], g[stat], g["opp"])]


def actual_value(sport: str, player: str, market: str, date: str) -> float | None:
    """The player's realized stat for a prop market on a given date, from the
    ingested box-score logs — used to grade logged prop plays. None if the box
    score hasn't been ingested yet or the market has no stat mapping."""
    info = market_to_stat(market)
    if info is None:
        return None
    stat, _role = info
    df = _logs(sport, (int(str(date)[:4]),))
    if df.empty or stat not in df.columns:
        return None
    g = df[df["norm"] == normalize(player)].copy()
    if g.empty:
        return None
    g["_d"] = pd.to_datetime(g["date"], errors="coerce").dt.normalize()
    g = g[g["_d"] == pd.to_datetime(date).normalize()]
    if g.empty:
        return None
    v = g.iloc[0][stat]
    return float(v) if pd.notna(v) else None


def _ingested_pks(sport: str) -> set:
    path = FORWARD_DIR / f"{sport.lower()}.jsonl"
    pks = set()
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip():
                pk = json.loads(line).get("game_pk")
                if pk is not None:
                    pks.add(pk)
    return pks


def ingest_mlb(date: str) -> int:
    """Fetch box scores for MLB games that finished on `date` and append new
    player lines to the forward store (skips already-ingested game_pks).
    Returns the number of player-rows added. Keeps heatmaps current as the
    season moves past the imported backfill."""
    from .clients import mlb_statsapi

    season = int(date[:4])
    done = _ingested_pks("mlb")
    rows = []
    for g in mlb_statsapi.final_scores(date):
        pk = g.get("game_pk")
        if pk is None or pk in done:
            continue
        for r in mlb_statsapi.box_player_logs(pk):
            if not r.get("name"):
                continue
            r.update({"date": date, "season": season})
            rows.append(r)
    append_logs("mlb", rows)
    return len(rows)


def ingest_espn(sport: str, date: str) -> int:
    """Fetch ESPN box scores for finished games on `date` (WNBA/NBA/NHL) and
    append new player lines to the forward store, idempotent by event id."""
    from .clients import espn

    season = int(date[:4])
    done = _ingested_pks(sport)
    rows = []
    try:
        finals = espn.results_range(sport, date, date)
    except Exception:
        finals = []
    for g in finals:
        eid = g.get("game_id")
        if not eid or eid in done:
            continue
        for r in espn.box_player_logs(sport, eid):
            if not r.get("name"):
                continue
            r.update({"date": date, "season": season})
            rows.append(r)
    append_logs(sport, rows)
    return len(rows)


def ingest(sport: str, date: str) -> int:
    """Append finished-game player logs for any supported sport."""
    if sport == "MLB":
        return ingest_mlb(date)
    if sport in ("WNBA", "NBA", "NFL", "NCAAF"):
        return ingest_espn(sport, date)
    return 0


def append_logs(sport: str, rows: list[dict]):
    """Append newly-completed game logs to the forward store (used by the
    hourly job). rows: [{name, date, season, opponent, <stat fields>}]."""
    if not rows:
        return
    FORWARD_DIR.mkdir(parents=True, exist_ok=True)
    path = FORWARD_DIR / f"{sport.lower()}.jsonl"
    with path.open("a") as f:
        for r in rows:
            f.write(json.dumps(r, default=str) + "\n")
    _logs.cache_clear()
