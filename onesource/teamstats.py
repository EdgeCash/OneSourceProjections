"""Team rolling splits and league ranks for the game research cards.

Builds one row per team-game from our box-score / game logs, then derives
Season / Home / Away / L10 / L5 splits and league ranks as of a date, plus
a matchup structure (each team's offense vs the other's defense) with an
advantage flag per stat. No lookahead beyond the imported data; the hourly
forward store keeps the current season fresh.

Stat coverage is what our data supports cleanly (a few reference stats like
WNBA paint points / fast break aren't in the logs and are omitted).
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd

from . import history, teams
from .names import normalize

# Matchup pairs: (label, offense_col, opponent_defense_col_or_None, off_dir).
# Each row compares a team's offense stat to the opponent's matching
# "allowed" stat; def_col is None where we have no allowed version.
WNBA_PAIRS = [
    ("PPG", "pts", "opp_pts", "high"),
    ("2PT%", "fg2_pct", "opp_fg2_pct", "high"),
    ("3PT%", "fg3_pct", "opp_fg3_pct", "high"),
    ("FT%", "ft_pct", None, "high"),
    ("REB", "reb", "opp_reb", "high"),
    ("AST", "ast", None, "high"),
    ("BLK+STL", "stocks", None, "high"),
    ("TOV", "tov", None, "low"),
]
MLB_PAIRS = [
    ("Runs/G", "runs", "opp_runs", "high"),
    ("Hits/G", "hits", "opp_hits", "high"),
    ("HR/G", "hr", None, "high"),
    ("Batter K/G", "k", "pk", "low"),
    ("1st Inn R/G", "f1", "opp_f1", "high"),
]
MLB_TRENDS = [
    ("NRFI%", "nrfi"), ("F5 Win%", "f5_win"), ("RL Cover%", "rl_cover"),
    ("Over%", "over_hit"), ("Pythag%", "pythag"),
]

STAT_SPECS = {
    "WNBA": {"pairs": WNBA_PAIRS},
    "MLB": {"pairs": MLB_PAIRS, "trends": MLB_TRENDS},
}

# ranking direction per column ("low" = lower is better)
DIRECTIONS = {
    # offense (high good)
    "pts": "high", "fg2_pct": "high", "fg3_pct": "high", "ft_pct": "high",
    "reb": "high", "ast": "high", "stocks": "high", "runs": "high",
    "hits": "high", "ba": "high", "hr": "high", "f1": "high",
    # offense (low good)
    "tov": "low", "k": "low",
    # defense / allowed (low good) + pitcher K (high good)
    "opp_pts": "low", "opp_fg2_pct": "low", "opp_fg3_pct": "low",
    "opp_reb": "low", "opp_runs": "low", "opp_hits": "low", "opp_f1": "low",
    "pk": "high",
}


@lru_cache(maxsize=4)
def team_games(sport: str, seasons: tuple[int, ...]) -> pd.DataFrame:
    return (_wnba_team_games(seasons) if sport == "WNBA"
            else _mlb_team_games(seasons))


def _wnba_team_games(seasons) -> pd.DataFrame:
    df = history.player_games("wnba", seasons=list(seasons))
    if df.empty:
        return pd.DataFrame()
    g = df.groupby(["game_id", "team"], as_index=False).agg(
        date=("date", "first"), season=("season", "first"),
        opp=("opponent", "first"), is_home=("is_home", "first"),
        pts=("points", "sum"), fgm=("fg_made", "sum"), fga=("fg_att", "sum"),
        tpm=("three_made", "sum"), tpa=("three_att", "sum"),
        ftm=("ft_made", "sum"), fta=("ft_att", "sum"),
        reb=("rebounds", "sum"), ast=("assists", "sum"),
        stl=("steals", "sum"), blk=("blocks", "sum"), tov=("turnovers", "sum"),
    )
    g["fg2_pct"] = ((g["fgm"] - g["tpm"]) / (g["fga"] - g["tpa"]).replace(0, np.nan))
    g["fg3_pct"] = g["tpm"] / g["tpa"].replace(0, np.nan)
    g["ft_pct"] = g["ftm"] / g["fta"].replace(0, np.nan)
    g["stocks"] = g["stl"] + g["blk"]
    # join opponent's offense in the same game as this team's "allowed"
    opp = g[["game_id", "team", "pts", "fg2_pct", "fg3_pct", "reb"]].rename(
        columns={"team": "opp_team", "pts": "opp_pts", "fg2_pct": "opp_fg2_pct",
                 "fg3_pct": "opp_fg3_pct", "reb": "opp_reb"})
    merged = g.merge(opp, left_on=["game_id", "opp"], right_on=["game_id", "opp_team"],
                     how="left")
    merged["team"] = merged["team"].map(lambda t: teams.canon("WNBA", t))
    return merged.sort_values("date")


def _mlb_team_games(seasons) -> pd.DataFrame:
    games = history.backfill_games("mlb", seasons=list(seasons))
    if games.empty:
        return pd.DataFrame()
    rows = []
    for _, r in games.iterrows():
        for side, opp_side in (("home", "away"), ("away", "home")):
            rows.append({
                "game_pk": r.get("game_pk"), "date": str(r["date"])[:10],
                "season": r.get("season"),
                "team": teams.canon("MLB", r[f"{side}_team"]),
                "opp": teams.canon("MLB", r[f"{opp_side}_team"]),
                "is_home": side == "home",
                "runs": r[f"{side}_score"], "opp_runs": r[f"{opp_side}_score"],
                "f1": r.get(f"f1_{side}"), "opp_f1": r.get(f"f1_{opp_side}"),
                "nrfi": 1.0 if r.get("nrfi") else 0.0,
                "over_hit": (1.0 if (r.get("total_runs") or 0) > (r.get("total") or 99)
                             else 0.0) if r.get("total") else np.nan,
                "rl_cover": 1.0 if r.get("rl_favorite_covered") else 0.0,
                "f5_win": (1.0 if r.get("f5_winner") == r[f"{side}_team"] else 0.0)
                          if r.get("f5_winner") else np.nan,
            })
    df = pd.DataFrame(rows)
    df["pythag"] = np.nan  # filled in splits via aggregate runs

    # batting/pitching from player box logs (group per team-game)
    pg = history.player_games("mlb", seasons=list(seasons))
    if not pg.empty:
        bat = pg[pg["position"] != "P"]
        pit = pg[pg["position"] == "P"]
        bstat = _explode_stats(bat, ["hits", "atBats", "homeRuns", "strikeOuts"])
        b = bstat.groupby(["game_pk", "team"], as_index=False).agg(
            hits=("hits", "sum"), ab=("atBats", "sum"),
            hr=("homeRuns", "sum"), k=("strikeOuts", "sum"))
        b["ba"] = b["hits"] / b["ab"].replace(0, np.nan)
        b["team"] = b["team"].map(lambda t: teams.canon("MLB", t))
        pstat = _explode_stats(pit, ["strikeOuts"])
        p = pstat.groupby(["game_pk", "team"], as_index=False).agg(pk=("strikeOuts", "sum"))
        p["team"] = p["team"].map(lambda t: teams.canon("MLB", t))
        df = df.merge(b, on=["game_pk", "team"], how="left")
        df = df.merge(p, on=["game_pk", "team"], how="left")
        # opponent hits allowed
        opp_h = b[["game_pk", "team", "hits"]].rename(
            columns={"team": "opp", "hits": "opp_hits"})
        df = df.merge(opp_h, on=["game_pk", "opp"], how="left")
    return df.sort_values("date")


def _explode_stats(df: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    out = df[["game_pk", "team"]].copy()
    for k in keys:
        out[k] = df["stats"].map(lambda s, kk=k: (s or {}).get(kk))
    return out


def _window(df: pd.DataFrame, team: str, asof: str) -> pd.DataFrame:
    d = df[(df["team"] == team) & (df["date"].astype(str) < asof)]
    return d.sort_values("date")


def splits(sport: str, df: pd.DataFrame, team: str, asof: str) -> dict:
    """{stat_col: {season, home, away, l10, l5}} for one team as of a date."""
    d = _window(df, team, asof)
    if d.empty:
        return {}
    cur = d["season"].max()
    season = d[d["season"] == cur]
    out: dict = {}
    cols = [c for c in d.columns if d[c].dtype.kind in "fi"
            and c not in ("game_pk", "season")]
    for c in cols:
        out[c] = {
            "season": _mean(season[c]),
            "home": _mean(season[season["is_home"]][c]),
            "away": _mean(season[~season["is_home"]][c]),
            "l10": _mean(d[c].tail(10)),
            "l5": _mean(d[c].tail(5)),
        }
    return out


def _mean(s: pd.Series):
    s = pd.to_numeric(s, errors="coerce").dropna()
    return round(float(s.mean()), 3) if len(s) else None


def league_ranks(sport: str, df: pd.DataFrame, asof: str, window: str = "l5") -> dict:
    """{stat_col: {team: rank}} ranking all teams by the chosen window."""
    teams = [t for t in df["team"].dropna().unique() if not str(t).isdigit()]
    per_team = {t: splits(sport, df, t, asof) for t in teams}
    cols = set()
    for s in per_team.values():
        cols.update(s.keys())
    direction = _directions(sport)
    ranks: dict = {}
    for c in cols:
        vals = [(t, per_team[t][c][window]) for t in teams
                if c in per_team[t] and per_team[t][c][window] is not None]
        if not vals:
            continue
        higher_better = direction.get(c, "high") != "low"
        vals.sort(key=lambda kv: kv[1], reverse=higher_better)
        ranks[c] = {t: i + 1 for i, (t, _) in enumerate(vals)}
    return ranks


def _directions(sport: str) -> dict:
    return DIRECTIONS


def matchup(sport: str, home: str, away: str, asof: str,
            seasons: tuple[int, ...] | None = None) -> dict:
    """Build the research-card comparison: each team's offense lines vs the
    opponent's defense, with split values, league ranks, and an advantage
    flag (1-3 stars by rank gap)."""
    seasons = seasons or _default_seasons(asof)
    df = team_games(sport, seasons)
    if df.empty:
        return {}
    home_k, away_k = teams.canon(sport, home), teams.canon(sport, away)
    ranks = league_ranks(sport, df, asof, "l5")
    hs, as_ = splits(sport, df, home_k, asof), splits(sport, df, away_k, asof)
    specs = STAT_SPECS[sport]

    def rows(off_team, off_split, off_home, def_team, def_split, def_home):
        """One row per stat pair. Each side carries season / situational
        (home or away, per where the team plays this game) / L10 / L5 / rank,
        so the card can show the full split spread the way the mockups do."""
        off_situ = "home" if off_home else "away"
        def_situ = "home" if def_home else "away"
        out = []
        for label, col, dcol, _dir in specs["pairs"]:
            o = off_split.get(col, {})
            o_rank = ranks.get(col, {}).get(off_team)
            row = {"stat": label,
                   "off_season": o.get("season"), "off_situ": o.get(off_situ),
                   "off_situ_label": off_situ.upper(), "off_l10": o.get("l10"),
                   "off_l5": o.get("l5"), "off_rank": o_rank,
                   "def_season": None, "def_situ": None, "def_situ_label": None,
                   "def_l10": None, "def_l5": None, "def_rank": None, "adv": 0}
            if dcol:
                d = def_split.get(dcol, {})
                d_rank = ranks.get(dcol, {}).get(def_team)
                row.update({"def_season": d.get("season"), "def_situ": d.get(def_situ),
                            "def_situ_label": def_situ.upper(), "def_l10": d.get("l10"),
                            "def_l5": d.get("l5"), "def_rank": d_rank,
                            "adv": _advantage(o_rank, d_rank)})
            out.append(row)
        return out

    return {
        "home": home, "away": away,
        "home_form": team_form(sport, df, home_k, asof),
        "away_form": team_form(sport, df, away_k, asof),
        "away_off_vs_home_def": rows(away_k, as_, False, home_k, hs, True),
        "home_off_vs_away_def": rows(home_k, hs, True, away_k, as_, False),
        "trends": _trends(sport, hs, as_),
        "n_teams": len([t for t in df["team"].dropna().unique() if not str(t).isdigit()]),
    }


def team_form(sport: str, df: pd.DataFrame, team: str, asof: str,
              n: int = 5) -> dict:
    """Current-season record, win/loss streak, and last-n results (opponent
    + score) for the card header — all from the same game logs."""
    d = _window(df, team, asof)
    if d.empty:
        return {}
    fc, oc = ("runs", "opp_runs") if sport == "MLB" else ("pts", "opp_pts")
    if fc not in d.columns or oc not in d.columns:
        return {}
    cur = d[d["season"] == d["season"].max()]
    won = pd.to_numeric(cur[fc], errors="coerce") > pd.to_numeric(cur[oc], errors="coerce")
    wins, losses = int(won.sum()), int((~won).sum())
    seq = list(won)[::-1]  # most recent first
    streak = ""
    if seq:
        first = seq[0]
        run = 0
        for v in seq:
            if v == first:
                run += 1
            else:
                break
        streak = f"{'W' if first else 'L'}{run}"
    last = d.tail(n)
    last5 = []
    for _, r in last.iterrows():
        f, o = r.get(fc), r.get(oc)
        if pd.isna(f) or pd.isna(o):
            continue
        last5.append({"opp": r.get("opp"), "win": bool(f > o),
                      "score": f"{int(f)}-{int(o)}"})
    return {"w": wins, "l": losses, "streak": streak, "last5": last5}


def _trends(sport, hs, as_) -> list:
    out = []
    for label, col in STAT_SPECS.get(sport, {}).get("trends", []):
        out.append({"stat": label, "home": (hs.get(col) or {}).get("season"),
                    "away": (as_.get(col) or {}).get("season")})
    return out


def _advantage(off_rank, def_rank) -> int:
    """Stars (0-3) when a team's offense rank meaningfully beats the
    opposing defense rank."""
    if off_rank is None or def_rank is None:
        return 0
    gap = def_rank - off_rank  # positive = offense ranked better than defense
    if gap >= 12:
        return 3
    if gap >= 7:
        return 2
    if gap >= 3:
        return 1
    return 0


def _default_seasons(asof: str) -> tuple[int, ...]:
    y = int(asof[:4])
    return (y - 1, y)
