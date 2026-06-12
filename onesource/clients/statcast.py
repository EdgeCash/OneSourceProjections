"""pybaseball / Statcast wrappers with disk caching. These power the
quality-of-contact adjustments (xBA, xwOBA, barrel rate) and K rates."""

from __future__ import annotations

import pandas as pd

from ..cache import cached_df

_TTL = 12 * 60 * 60


def _pybaseball():
    import pybaseball

    pybaseball.cache.enable()
    return pybaseball


def season_batting(season: int, min_pa: int = 50) -> pd.DataFrame:
    """FanGraphs season batting (includes K%, BB%, ISO, wOBA...)."""
    def fetch():
        pb = _pybaseball()
        df = pb.batting_stats(season, qual=min_pa)
        return df[
            [c for c in ("Name", "Team", "PA", "AB", "H", "HR", "AVG", "SLG",
                         "K%", "BB%", "ISO", "wOBA") if c in df.columns]
        ]

    return cached_df(f"pb:bat:{season}:{min_pa}", _TTL, fetch)


def season_pitching(season: int, min_ip: int = 10) -> pd.DataFrame:
    """FanGraphs season pitching (K/9, xFIP, ERA, IP/GS...)."""
    def fetch():
        pb = _pybaseball()
        df = pb.pitching_stats(season, qual=min_ip)
        return df[
            [c for c in ("Name", "Team", "IP", "GS", "G", "ERA", "xFIP", "FIP",
                         "K/9", "BB/9", "K%", "SO") if c in df.columns]
        ]

    return cached_df(f"pb:pit:{season}:{min_ip}", _TTL, fetch)


def statcast_batter_expected(season: int) -> pd.DataFrame:
    """Statcast expected stats leaderboard: xBA, xSLG, xwOBA, barrel%."""
    def fetch():
        pb = _pybaseball()
        df = pb.statcast_batter_expected_stats(season, minPA=25)
        return df

    return cached_df(f"sc:batexp:{season}", _TTL, fetch)


def statcast_pitcher_expected(season: int) -> pd.DataFrame:
    def fetch():
        pb = _pybaseball()
        df = pb.statcast_pitcher_expected_stats(season, minPA=25)
        return df

    return cached_df(f"sc:pitexp:{season}", _TTL, fetch)
