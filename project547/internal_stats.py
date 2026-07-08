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
                 "baseOnBalls", "hitByPitch", "homeRuns", "hits", "earnedRuns"]
_BAT_FIELDS = ["hits", "totalBases", "homeRuns", "atBats", "plateAppearances",
               "baseOnBalls", "strikeOuts"]


def _ip_to_float(v):
    """inningsPitched arrives as float thirds (3.667) in some seasons and
    as statsapi '5.2' strings (= 5 and 2/3) in others."""
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip()
        if s.count(".") == 1:
            whole, frac = s.split(".")
            if frac in ("0", "1", "2") and whole.isdigit():
                return int(whole) + int(frac) / 3.0
        try:
            return float(s)
        except ValueError:
            return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _mlb_rows(season: int) -> pd.DataFrame:
    """Flat per-player-game rows for a season from backfill (nested stats)
    plus the forward store (flat), with name/team/position/started/ids. All
    stat columns are numeric-coerced (sources mix strings and numbers).

    De-duplication (audit #9): the old key (norm_name, date) deleted the
    second game of every doubleheader and collapsed distinct same-named
    players. The key is now (player_id, game_pk) — falling back to norm_name
    / date only for the component a row is missing (early forward-store rows
    carry game_pk but no player_id; both sources normally carry both) — and
    id-less rows that duplicate an id-carrying row of the same (name, game)
    are dropped so overlapping backfill/forward coverage can't double-count."""
    frames = []
    bf = history.player_games("mlb", seasons=[season])
    if not bf.empty:
        flat = pd.DataFrame({
            "name": bf["player_name"], "player_id": bf.get("player_id"),
            "team": bf.get("team"), "position": bf.get("position"),
            "started": bf.get("started"), "date": bf["date"],
            "game_pk": bf.get("game_pk"),
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
                "game_pk": raw.get("game_pk"),
            })
            for f in set(_PITCH_FIELDS + _BAT_FIELDS):
                keep[f] = raw.get(f)
            frames.append(keep)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["inningsPitched"] = df["inningsPitched"].map(_ip_to_float)
    for f in set(_PITCH_FIELDS + _BAT_FIELDS) - {"inningsPitched"}:
        df[f] = pd.to_numeric(df[f], errors="coerce")
    df["player_id"] = pd.to_numeric(df["player_id"], errors="coerce")
    df["game_pk"] = pd.to_numeric(df["game_pk"], errors="coerce")
    df["norm_name"] = df["name"].map(normalize)
    df["date"] = pd.to_datetime(df["date"])
    # player key: id where present (separates same-named players), else name;
    # game key: game_pk where present (keeps doubleheaders), else the date.
    pkey = df["player_id"].astype("object").where(
        df["player_id"].notna(), "n:" + df["norm_name"].astype(str))
    gkey = df["game_pk"].astype("object").where(
        df["game_pk"].notna(), df["date"].astype(str))
    df = df.loc[~pd.DataFrame({"p": pkey, "g": gkey}).duplicated(keep="last")]
    # drop id-less rows shadowing an id-carrying row of the same (name, game)
    has_id = df["player_id"].notna()
    gkey = gkey.loc[df.index]
    seen = set(zip(df.loc[has_id, "norm_name"], gkey[has_id]))
    shadow = ~has_id & pd.Series(
        list(zip(df["norm_name"], gkey)), index=df.index).isin(seen)
    return df.loc[~shadow].reset_index(drop=True)


def _fip(hr, bb, hbp, k, ip, prior_ip: float) -> float:
    raw = (13 * hr + 3 * (bb + hbp) - 2 * k) / ip + FIP_CONST
    return (raw * ip + LEAGUE_FIP * prior_ip) / (ip + prior_ip)


@lru_cache(maxsize=4)
def _reliever_daily(season: int) -> pd.DataFrame:
    """Per (team, date) relief innings — the input to bullpen-fatigue."""
    df = _mlb_rows(season)
    if df.empty:
        return pd.DataFrame(columns=["team_c", "date", "ip"])
    rp = df[(df["position"] == "P") & (df["started"] == False)].copy()  # noqa: E712
    if rp.empty:
        return pd.DataFrame(columns=["team_c", "date", "ip"])
    rp["team_c"] = rp["team"].map(lambda t: teams.canon("MLB", str(t)))
    daily = rp.groupby(["team_c", "date"], as_index=False)["inningsPitched"].sum()
    return daily.rename(columns={"inningsPitched": "ip"})


def bullpen_fatigue(season: int, team: str, asof: str, days: int = 2) -> dict:
    """How hard a team's bullpen has worked in the ``days`` before ``asof``.
    Returns {ip, appearances_days, level}: level in
    'fresh' / 'moderate' / 'heavy' (>= ~4.5 relief IP over 2 days, or work on
    both prior days, taxes a pen). Empty dict when unknown."""
    daily = _reliever_daily(season)
    if daily.empty:
        return {}
    tc = teams.canon("MLB", str(team))
    lo = (pd.to_datetime(asof) - pd.Timedelta(days=days)).strftime("%Y-%m-%d")
    d = pd.to_datetime(daily["date"], errors="coerce")
    sub = daily[(daily["team_c"] == tc) & (d < pd.to_datetime(asof)) & (d >= lo)]
    if sub.empty:
        return {}
    ip = round(float(pd.to_numeric(sub["ip"], errors="coerce").sum()), 1)
    n_days = int(sub["date"].nunique())
    level = "heavy" if (ip >= 4.5 or n_days >= days) else (
        "moderate" if ip >= 2.5 else "fresh")
    return {"ip": ip, "appearances_days": n_days, "level": level}


def _player_key(df: pd.DataFrame) -> pd.Series:
    """Aggregation key: player_id where present (distinguishes same-named
    players), normalized name otherwise. The frames returned to consumers keep
    a norm_name column, so name-keyed lookups still work."""
    return df["player_id"].astype("object").where(
        df["player_id"].notna(), "n:" + df["norm_name"].astype(str))


@lru_cache(maxsize=4)
def pitcher_table(season: int) -> pd.DataFrame:
    """Starter rates: Name, norm_name, FIP, K%, IP, GS."""
    df = _mlb_rows(season)
    if df.empty:
        return pd.DataFrame(columns=["Name", "norm_name"])
    sp = df[(df["position"] == "P") & (df["started"] == True)]  # noqa: E712
    if sp.empty:
        return pd.DataFrame(columns=["Name", "norm_name"])
    agg = sp.groupby(_player_key(sp)).agg(
        Name=("name", "first"), norm_name=("norm_name", "first"),
        player_id=("player_id", "first"),
        k=("strikeOuts", "sum"), bf=("battersFaced", "sum"),
        ip=("inningsPitched", "sum"), bb=("baseOnBalls", "sum"),
        hbp=("hitByPitch", "sum"), hr=("homeRuns", "sum"),
        hits=("hits", "sum"), er=("earnedRuns", "sum"),
        GS=("date", "count"),
    ).reset_index(drop=True)
    agg = agg[agg["ip"] > 0]
    agg["FIP"] = [
        round(_fip(r.hr or 0, r.bb or 0, r.hbp or 0, r.k or 0, r.ip, SP_IP_PRIOR), 3)
        for r in agg.itertuples()]
    agg["K%"] = (agg["k"] / agg["bf"].replace(0, np.nan)).round(4)
    # Per-inning rates so the outs/hits/ER/walks prop models use the pitcher's
    # own numbers instead of league fallbacks (H/9, BB/9, ERA, BB%).
    ip = agg["ip"].replace(0, np.nan)
    agg["H/9"] = (agg["hits"] / ip * 9).round(3)
    agg["BB/9"] = (agg["bb"] / ip * 9).round(3)
    agg["ERA"] = (agg["er"] / ip * 9).round(3)
    agg["BB%"] = (agg["bb"] / agg["bf"].replace(0, np.nan)).round(4)
    agg["IP"] = agg["ip"]
    return agg[["Name", "norm_name", "player_id", "FIP", "K%", "H/9", "BB/9",
                "ERA", "BB%", "IP", "GS"]]


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
    agg = bat.groupby(_player_key(bat)).agg(
        Name=("name", "first"), norm_name=("norm_name", "first"),
        player_id=("player_id", "first"),
        H=("hits", "sum"), TB=("totalBases", "sum"), HR=("homeRuns", "sum"),
        AB=("atBats", "sum"), PA=("plateAppearances", "sum"),
        BB=("baseOnBalls", "sum"), K=("strikeOuts", "sum"),
    ).reset_index(drop=True)
    agg = agg[agg["AB"] > 0]
    agg["AVG"] = (agg["H"] / agg["AB"]).round(4)
    agg["SLG"] = (agg["TB"] / agg["AB"]).round(4)
    # plate-discipline / power rates (context beyond AVG/SLG)
    pa = agg["PA"].replace(0, np.nan)
    agg["ISO"] = (agg["SLG"] - agg["AVG"]).round(4)   # raw power
    agg["BB%"] = (agg["BB"] / pa).round(4)
    agg["K%"] = (agg["K"] / pa).round(4)
    x = history.statcast_xstats(season - 1).get("batting", {})
    if x:
        def look(pid, key):
            try:
                return x.get(str(int(pid)), {}).get(key)
            except (TypeError, ValueError):
                return None
        agg["est_ba"] = agg["player_id"].map(lambda p: look(p, "xba"))
        agg["est_slg"] = agg["player_id"].map(lambda p: look(p, "xslg"))
    return agg[[c for c in ("Name", "norm_name", "player_id", "AVG", "SLG",
                            "ISO", "BB%", "K%", "PA", "HR", "est_ba", "est_slg")
                if c in agg.columns]]


def clear_caches():
    pitcher_table.cache_clear()
    bullpen_fip.cache_clear()
    batter_table.cache_clear()
    _reliever_daily.cache_clear()
