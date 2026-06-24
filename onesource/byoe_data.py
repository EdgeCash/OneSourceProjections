"""Adapter: OneSource historical data -> BYOE inputs.

Keeps `onesource/byoe.py` pure (no pandas). This turns `teamstats.team_games`
into (a) a list of games the BYOE engine can grade and (b) a walk-forward
z-index function that, for any date, builds league z-scores from each team's
season-to-date stats *before* that date — point-in-time, no leakage.
"""

from __future__ import annotations

import pandas as pd

from . import byoe, teamstats

# Stats every team_games frame carries; richer columns (yardage) are added when
# present and non-null. "opp_pts" lower = better defense, so weight it negative.
BASE_STATS = ("pts", "opp_pts")


def available_stats(df: pd.DataFrame) -> list[str]:
    """Numeric stat columns with real (non-all-null) data, minus bookkeeping."""
    skip = {"season"}
    out = []
    for c in df.columns:
        if c in skip or str(df[c].dtype) not in ("int64", "float64"):
            continue
        if df[c].notna().any():
            out.append(c)
    return out


def games(df: pd.DataFrame) -> list[dict]:
    """One dict per game (home perspective): date, home, away, scores."""
    rows = []
    for gid, grp in df.groupby("game_id"):
        home = grp[grp["is_home"]]
        if home.empty:
            continue
        h = home.iloc[0]
        rows.append({"date": h["date"], "home": h["team"], "away": h["opp"],
                     "home_score": float(h["pts"]), "away_score": float(h["opp_pts"])})
    rows.sort(key=lambda r: r["date"])
    return rows


def walk_forward_zindex(df: pd.DataFrame, stat_keys):
    """Return ``z_index_fn(date)`` that builds a league z-index from each team's
    mean of ``stat_keys`` over its same-season games strictly before ``date``."""
    df = df.sort_values("date")

    def fn(date: str) -> dict[str, dict[str, float]]:
        season = int(str(date)[:4])
        prior = df[(df["date"] < date) & (df["season"] == season)]
        if prior.empty:
            # fall back to anything before the date (early season)
            prior = df[df["date"] < date]
        team_stats: dict[str, dict[str, float]] = {}
        for team, g in prior.groupby("team"):
            team_stats[team] = {k: float(g[k].mean()) for k in stat_keys
                                if k in g and g[k].notna().any()}
        return byoe.zscore_index(team_stats)

    return fn


def backtest_edge(edge: byoe.Edge, sport_key: str, seasons: tuple[int, ...]):
    """End-to-end: load games for a sport+seasons and walk-forward grade an edge.
    Returns a ``byoe.BacktestResult``."""
    from .sports import SPORTS
    df = teamstats.team_games(sport_key, seasons)
    stat_keys = [i.stat_key for i in edge.inputs]
    zfn = walk_forward_zindex(df, stat_keys)
    return byoe.backtest(edge, SPORTS[sport_key], games(df), zfn)
