"""Season-to-date MLB player/team rates computed from our own box-score
logs (backfill + the hourly forward store). Replaces the FanGraphs/
pybaseball live dependency, which is blocked (403) on CI runners.

Provides the three tables the live pipeline needs:
  - pitcher_table(season): per starter — FIP (shrunk), K%, IP/GS
  - bullpen_fip(season):   per team   — relief FIP (shrunk)
  - batter_table(season):  per batter — AVG/SLG/PA/HR + prior-season
                           Statcast xBA/xSLG
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd

from . import history, playerlogs, teams
from .names import normalize

LEAGUE_FIP = 4.10
FIP_CONST = 3.10
SP_IP_PRIOR = 50.0
BP_IP_PRIOR = 120.0

_PITCH_FIELDS = ["strikeOuts", "battersFaced", "inningsPitched",
                 "baseOnBalls", "hitByPitch", "homeRuns"]
_BAT_FIELDS = ["hits", "totalBases", "homeRuns", "atBats", "plateAppearances"]


def _mlb_rows(season: int) -> pd.DataFrame:
    """Flat per-player-game rows for a season from backfill (nested stats)
    plus the forward store (flat), with name/team/position/started."""
    frames = []
    bf = history.player_games("mlb", seasons=[season])
    if not bf.empty:
        flat = pd.DataFrame({
            "name": bf["player_name"], "player_id": bf.get("player_id"),
            "team": bf.get("team"), "position": bf.get("position"),
            "started": bf.get("started"), "date": bf["date"],
        })
        for f in set(_PITCH_FIELDS + _BAT_FIELDS):
            flat[f] = bf["stats"].map(lambda s, k=f: (s or {}).get(k))
        frames.append(flat)
    fwd = playerlogs.FORWARD_DIR / "mlb.jsonl"
    if fwd.exists():
        raw = pd.read_json(fwd, lines=True)
        if "season" in raw.columns:
            raw = raw[raw["season"] == season]
        if not raw.empty:
            keep = pd.DataFrame({
                "name": raw.get("name"), "player_id": raw.get("player_id"),
                "team": raw.get("team"), "position": raw.get("position"),
                "started": raw.get("started"), "date": raw.get("date"),
            })
            for f in set(_PITCH_FIELDS + _BAT_FIELDS):
                keep[f] = raw.get(f)
            frames.append(keep)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["norm_name"] = df["name"].map(normalize)
    df["date"] = pd.to_datetime(df["date"])
    return df.drop_duplicates(["norm_name", "date"], keep="last")


def _fip(hr, bb, hbp, k, ip, prior_ip: float) -> float:
    raw = (13 * hr + 3 * (bb + hbp) - 2 * k) / ip + FIP_CONST
    return (raw * ip + LEAGUE_FIP * prior_ip) / (ip + prior_ip)


@lru_cache(maxsize=4)
def pitcher_table(season: int) -> pd.DataFrame:
    """Starter rates: Name, norm_name, FIP, K%, IP, GS."""
    df = _mlb_rows(season)
    if df.empty:
        return pd.DataFrame(columns=["Name", "norm_name"])
    sp = df[(df["position"] == "P") & (df["started"] == True)]  # noqa: E712
    if sp.empty:
        return pd.DataFrame(columns=["Name", "norm_name"])
    agg = sp.groupby("norm_name").agg(
        Name=("name", "first"),
        k=("strikeOuts", "sum"), bf=("battersFaced", "sum"),
        ip=("inningsPitched", "sum"), bb=("baseOnBalls", "sum"),
        hbp=("hitByPitch", "sum"), hr=("homeRuns", "sum"),
        GS=("date", "count"),
    ).reset_index()
    agg = agg[agg["ip"] > 0]
    agg["FIP"] = [
        round(_fip(r.hr or 0, r.bb or 0, r.hbp or 0, r.k or 0, r.ip, SP_IP_PRIOR), 3)
        for r in agg.itertuples()]
    agg["K%"] = (agg["k"] / agg["bf"].replace(0, np.nan)).round(4)
    agg["IP"] = agg["ip"]
    return agg[["Name", "norm_name", "FIP", "K%", "IP", "GS"]]


@lru_cache(maxsize=4)
def bullpen_fip(season: int) -> dict[str, float]:
    """Team relief-corps FIP (shrunk), keyed by canonical team."""
    df = _mlb_rows(season)
    if df.empty:
        return {}
    rp = df[(df["position"] == "P") & (df["started"] == False)]  # noqa: E712
    if rp.empty:
        return {}
    rp = rp.assign(team_c=rp["team"].map(lambda t: teams.canon("MLB", str(t))))
    out = {}
    for team, g in rp.groupby("team_c"):
        ip = g["inningsPitched"].sum()
        if ip and ip > 0:
            out[team] = round(_fip(g["homeRuns"].sum() or 0,
                                   g["baseOnBalls"].sum() or 0,
                                   g["hitByPitch"].sum() or 0,
                                   g["strikeOuts"].sum() or 0, ip, BP_IP_PRIOR), 3)
    return out


@lru_cache(maxsize=4)
def batter_table(season: int) -> pd.DataFrame:
    """Batter rates: Name, norm_name, AVG, SLG, PA, HR (+ prior-season
    Statcast est_ba/est_slg where the player id matches)."""
    df = _mlb_rows(season)
    if df.empty:
        return pd.DataFrame(columns=["Name", "norm_name"])
    bat = df[df["position"] != "P"]
    agg = bat.groupby("norm_name").agg(
        Name=("name", "first"), player_id=("player_id", "first"),
        H=("hits", "sum"), TB=("totalBases", "sum"), HR=("homeRuns", "sum"),
        AB=("atBats", "sum"), PA=("plateAppearances", "sum"),
    ).reset_index()
    agg = agg[agg["AB"] > 0]
    agg["AVG"] = (agg["H"] / agg["AB"]).round(4)
    agg["SLG"] = (agg["TB"] / agg["AB"]).round(4)
    x = history.statcast_xstats(season - 1).get("batting", {})
    if x:
        def look(pid, key):
            try:
                return x.get(str(int(pid)), {}).get(key)
            except (TypeError, ValueError):
                return None
        agg["est_ba"] = agg["player_id"].map(lambda p: look(p, "xba"))
        agg["est_slg"] = agg["player_id"].map(lambda p: look(p, "xslg"))
    return agg[[c for c in ("Name", "norm_name", "AVG", "SLG", "PA", "HR",
                            "est_ba", "est_slg") if c in agg.columns]]


def clear_caches():
    pitcher_table.cache_clear()
    bullpen_fip.cache_clear()
    batter_table.cache_clear()
