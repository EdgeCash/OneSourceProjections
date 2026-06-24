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


def games(df: pd.DataFrame, odds: dict | None = None) -> list[dict]:
    """One dict per game (home perspective): date, home, away, scores, game_id.

    When ``odds`` (from :func:`closing_odds_by_game`) is given, real closing
    spread/total/moneyline lines and american odds are attached by game_id so
    the backtest grades and prices against the actual market, not a -110 stub."""
    rows = []
    for gid, grp in df.groupby("game_id"):
        home = grp[grp["is_home"]]
        if home.empty:
            continue
        h = home.iloc[0]
        g = {"game_id": gid, "date": h["date"], "home": h["team"], "away": h["opp"],
             "home_score": float(h["pts"]), "away_score": float(h["opp_pts"])}
        if odds and gid in odds:
            g.update(odds[gid])
        rows.append(g)
    rows.sort(key=lambda r: r["date"])
    return rows


def _f(v) -> float | None:
    try:
        f = float(v)
        return None if f != f else f  # drop NaN
    except (TypeError, ValueError):
        return None


def closing_odds_by_game(sport: str, seasons: tuple[int, ...]) -> dict[str, dict]:
    """{game_id: {spread, spread_home_odds, spread_away_odds, total,
    total_over_odds, total_under_odds, home_ml, away_ml}} from the committed
    closing-line store. game_id matches teamstats' game_id (same
    YYYY_WW_AWAY_HOME key), so no team-name mapping is needed."""
    from . import history
    out: dict[str, dict] = {}
    for season in seasons:
        cl = history.closing_lines(sport, season)
        if cl is None or cl.empty:
            continue
        for gid, grp in cl.groupby("event_id"):
            d = out.setdefault(gid, {})
            for _, r in grp.iterrows():
                m, side = r.get("market"), r.get("side")
                line, ao = _f(r.get("line")), _f(r.get("american_odds"))
                if m == "spread":
                    if side == "home":
                        d["spread"], d["spread_home_odds"] = line, ao
                    else:
                        d["spread_away_odds"] = ao
                elif m == "total":
                    if side == "over":
                        d["total"], d["total_over_odds"] = line, ao
                    else:
                        d["total_under_odds"] = ao
                elif m == "moneyline":
                    d["home_ml" if side == "home" else "away_ml"] = ao
    return out


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


def backtest_edge(edge: byoe.Edge, sport_key: str, seasons: tuple[int, ...],
                  use_closing_odds: bool = True):
    """End-to-end: load games for a sport+seasons and walk-forward grade an edge.

    With ``use_closing_odds`` (default), real closing spread/total/moneyline
    lines + american odds are attached so ROI is graded against the actual
    market. Returns a ``byoe.BacktestResult``."""
    from .sports import SPORTS
    df = teamstats.team_games(sport_key, seasons)
    stat_keys = [i.stat_key for i in edge.inputs]
    zfn = walk_forward_zindex(df, stat_keys)
    odds = closing_odds_by_game(sport_key, seasons) if use_closing_odds else None
    return byoe.backtest(edge, SPORTS[sport_key], games(df, odds), zfn)
