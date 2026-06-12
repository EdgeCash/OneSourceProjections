"""Daily pipeline: build the slate, project games and props, pull market
lines from BettingPros, and compute edges.

Designed to degrade gracefully: if BettingPros or FantasyPros keys are
missing/unreachable, you still get model projections — just no market
comparison columns.
"""

from __future__ import annotations

import json
import logging
from datetime import date as _date

import pandas as pd

from . import config, odds
from .clients import bettingpros, fantasypros, mlb_statsapi, statcast
from .models import game as game_model
from .models import props as prop_model
from .names import normalize

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------

def _season(date: str) -> int:
    return int(date[:4])


def _team_runs_per_game(team_id: int, date: str) -> float:
    results = mlb_statsapi.team_recent_results(team_id, date, config.TEAM_FORM_GAMES)
    scored = [r["runs_scored"] for r in results if r["runs_scored"] is not None]
    if len(scored) < 5:
        return config.LEAGUE_RUNS_PER_GAME
    return sum(scored) / len(scored)


def _pitcher_table(season: int) -> pd.DataFrame:
    try:
        df = statcast.season_pitching(season)
        df = df.assign(norm_name=df["Name"].map(normalize))
        return df
    except Exception as e:  # early season / network issues
        log.warning("pitching stats unavailable: %s", e)
        return pd.DataFrame(columns=["Name", "norm_name"])


def _batter_table(season: int) -> pd.DataFrame:
    try:
        df = statcast.season_batting(season)
        df = df.assign(norm_name=df["Name"].map(normalize))
    except Exception as e:
        log.warning("batting stats unavailable: %s", e)
        return pd.DataFrame(columns=["Name", "norm_name"])
    try:
        exp = statcast.statcast_batter_expected(season)
        exp = exp.assign(
            norm_name=(exp["first_name"].str.strip() + " " + exp["last_name"].str.strip()).map(
                normalize
            )
        )[["norm_name", "est_ba", "est_slg"]]
        df = df.merge(exp, on="norm_name", how="left")
    except Exception as e:
        log.warning("statcast expected stats unavailable: %s", e)
    return df


def _fp_projections(season: int) -> dict[str, dict]:
    try:
        players = fantasypros.mlb_projections(season)
        return fantasypros.projection_index(players)
    except Exception as e:
        log.warning("FantasyPros projections unavailable: %s", e)
        return {}


def _lookup_float(d: dict, *keys: str) -> float | None:
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return None


# ---------------------------------------------------------------------------
# Game projections
# ---------------------------------------------------------------------------

def project_games(date: str) -> pd.DataFrame:
    season = _season(date)
    slate = mlb_statsapi.schedule(date)
    pitchers = _pitcher_table(season)

    def starter_xfip(name: str | None) -> float | None:
        if not name or pitchers.empty:
            return None
        row = pitchers[pitchers["norm_name"] == normalize(name)]
        if row.empty:
            return None
        return _lookup_float(row.iloc[0].to_dict(), "xFIP", "FIP", "ERA")

    rows = []
    for g in slate:
        home = game_model.TeamInputs(
            name=g["home_team"],
            runs_per_game=_team_runs_per_game(g["home_team_id"], date),
            opp_starter_xfip=starter_xfip(g["away_pitcher"]),
        )
        away = game_model.TeamInputs(
            name=g["away_team"],
            runs_per_game=_team_runs_per_game(g["away_team_id"], date),
            opp_starter_xfip=starter_xfip(g["home_pitcher"]),
        )
        proj = game_model.simulate(home, away)
        rows.append(
            {
                "game_pk": g["game_pk"],
                "game_time": g["game_time"],
                "away_team": g["away_team"],
                "home_team": g["home_team"],
                "away_pitcher": g["away_pitcher"],
                "home_pitcher": g["home_pitcher"],
                "home_exp_runs": proj.home_exp_runs,
                "away_exp_runs": proj.away_exp_runs,
                "proj_total": proj.total_mean,
                "home_win_prob": round(proj.home_win_prob, 4),
                "away_win_prob": round(1 - proj.home_win_prob, 4),
                "over_probs": proj.over_probs,
                "home_rl_cover": proj.home_runline_cover,
            }
        )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Prop projections
# ---------------------------------------------------------------------------

def project_props(date: str) -> pd.DataFrame:
    season = _season(date)
    slate = mlb_statsapi.schedule(date)
    pitchers = _pitcher_table(season)
    batters = _batter_table(season)
    fp = _fp_projections(season)

    rows: list[dict] = []
    for g in slate:
        rows += _pitcher_prop_rows(g, pitchers, fp, season)
        rows += _batter_prop_rows(g, batters, fp)
    return pd.DataFrame(rows)


def _pitcher_prop_rows(g: dict, pitchers: pd.DataFrame, fp: dict, season: int) -> list[dict]:
    rows = []
    for side, opp_side in (("home", "away"), ("away", "home")):
        name = g.get(f"{side}_pitcher")
        if not name:
            continue
        stats_row: dict = {}
        if not pitchers.empty:
            match = pitchers[pitchers["norm_name"] == normalize(name)]
            if not match.empty:
                stats_row = match.iloc[0].to_dict()

        k_rate = _lookup_float(stats_row, "K%")
        if k_rate is not None and k_rate > 1:  # FanGraphs returns 24.5 not 0.245
            k_rate /= 100.0
        ip, gs = _lookup_float(stats_row, "IP"), _lookup_float(stats_row, "GS")
        exp_innings = (ip / gs) if ip and gs else 5.3

        opp_team_id = g[f"{opp_side}_team_id"]
        opp_stats = mlb_statsapi.team_season_hitting(opp_team_id, season)
        opp_k = None
        so, pa = _lookup_float(opp_stats, "strikeOuts"), _lookup_float(opp_stats, "plateAppearances")
        if so and pa:
            opp_k = so / pa

        fp_stats = fp.get(normalize(name), {})
        fp_k_season = _lookup_float(fp_stats, "K", "SO", "strikeouts")
        fp_gs = _lookup_float(fp_stats, "GS", "G")
        fp_k_per_start = (fp_k_season / fp_gs) if fp_k_season and fp_gs else None

        model = prop_model.pitcher_strikeouts(exp_innings, k_rate, opp_k, fp_k_per_start)
        rows.append(
            {
                "game_pk": g["game_pk"],
                "market": "pitcher_strikeouts",
                "player": name,
                "team": g[f"{side}_team"],
                "opponent": g[f"{opp_side}_team"],
                "projection": round(model["mean"], 2),
                "dist": "poisson",
                "param": model["lambda"],
            }
        )
    return rows


def _batter_prop_rows(g: dict, batters: pd.DataFrame, fp: dict) -> list[dict]:
    rows = []
    try:
        lineups = mlb_statsapi.batting_order(g["game_pk"])
    except Exception:
        return rows
    for side, opp_side in (("home", "away"), ("away", "home")):
        for entry in lineups.get(side, []):
            name, slot = entry["name"], entry["slot"]
            stats_row: dict = {}
            if not batters.empty:
                match = batters[batters["norm_name"] == normalize(name)]
                if not match.empty:
                    stats_row = match.iloc[0].to_dict()

            exp_ab = prop_model.expected_ab_for_slot(slot)
            fp_stats = fp.get(normalize(name), {})
            games = _lookup_float(fp_stats, "G") or 150.0

            def per_game(*keys):
                season_total = _lookup_float(fp_stats, *keys)
                return season_total / games if season_total else None

            ba = _lookup_float(stats_row, "AVG")
            xba = _lookup_float(stats_row, "est_ba")
            hits = prop_model.batter_hits(exp_ab, ba, xba, per_game("H", "hits"))

            slg = _lookup_float(stats_row, "SLG")
            xslg = _lookup_float(stats_row, "est_slg")
            tb = prop_model.batter_total_bases(exp_ab, slg, xslg, per_game("TB"))

            pa_total = _lookup_float(stats_row, "PA")
            hr_total = _lookup_float(stats_row, "HR")
            hr_rate = hr_total / pa_total if hr_total and pa_total else None
            hr = prop_model.batter_home_run(exp_ab + 0.4, hr_rate, per_game("HR"))

            common = {
                "game_pk": g["game_pk"],
                "player": name,
                "team": g[f"{side}_team"],
                "opponent": g[f"{opp_side}_team"],
            }
            rows += [
                {**common, "market": "batter_hits", "projection": round(hits["mean"], 2),
                 "dist": "binomial", "param": hits["p"], "n": hits["n"]},
                {**common, "market": "batter_total_bases", "projection": round(tb["mean"], 2),
                 "dist": "poisson", "param": tb["lambda"]},
                {**common, "market": "batter_home_runs", "projection": round(hr["p_hr"], 3),
                 "dist": "bernoulli", "param": hr["p_hr"]},
            ]
    return rows


# ---------------------------------------------------------------------------
# Market comparison / edges
# ---------------------------------------------------------------------------

def prob_over_for_row(row: dict, line: float) -> float | None:
    if row["dist"] == "poisson":
        return prop_model.prob_over_count(row["param"], line)
    if row["dist"] == "binomial":
        return prop_model.prob_over_hits(row.get("n", 4), row["param"], line)
    if row["dist"] == "bernoulli":
        return row["param"] if line < 1 else None
    return None


def attach_prop_edges(props: pd.DataFrame, date: str) -> pd.DataFrame:
    """Join model projections to BettingPros prop offers and compute EV."""
    if props.empty:
        return props
    try:
        events = bettingpros.events("MLB", date)
        event_ids = [e.get("id") for e in events if e.get("id")]
    except Exception as e:
        log.warning("BettingPros events unavailable: %s", e)
        return props

    market_rows = []
    for market, mid in config.BP_MARKET_IDS.items():
        if not market.startswith(("pitcher_", "batter_")):
            continue
        try:
            offers = bettingpros.offers("MLB", mid, event_ids)
            for r in bettingpros.flatten_offers(offers):
                r["market"] = market
                market_rows.append(r)
        except Exception as e:
            log.warning("offers for %s unavailable: %s", market, e)

    if not market_rows:
        return props

    lines = pd.DataFrame(market_rows)
    lines = lines[lines["active"] & lines["odds"].notna() & lines["line"].notna()]
    lines["norm_player"] = lines["participant"].map(normalize)
    # best available "over" price per player+market
    overs = (
        lines[lines["selection"].str.lower().str.contains("over", na=False)]
        .sort_values("odds", ascending=False)
        .drop_duplicates(["norm_player", "market"])
    )

    props = props.copy()
    props["norm_player"] = props["player"].map(normalize)
    merged = props.merge(
        overs[["norm_player", "market", "line", "odds", "book_id"]],
        on=["norm_player", "market"],
        how="left",
    )

    def compute(row):
        if pd.isna(row.get("line")) or pd.isna(row.get("odds")):
            return pd.Series({"model_over_prob": None, "ev": None, "kelly": None})
        p = prob_over_for_row(row, float(row["line"]))
        if p is None:
            return pd.Series({"model_over_prob": None, "ev": None, "kelly": None})
        ev = odds.expected_value(p, float(row["odds"]))
        k = odds.kelly_stake(p, float(row["odds"]), config.KELLY_FRACTION)
        return pd.Series({"model_over_prob": round(p, 4), "ev": round(ev, 4),
                          "kelly": round(k, 4)})

    merged = pd.concat([merged, merged.apply(compute, axis=1)], axis=1)
    return merged.drop(columns=["norm_player"])


def attach_game_edges(games: pd.DataFrame, date: str) -> pd.DataFrame:
    """Join moneyline offers to game projections and compute EV both sides."""
    if games.empty:
        return games
    try:
        events = bettingpros.events("MLB", date)
        offers = bettingpros.offers(
            "MLB", config.BP_MARKET_IDS["moneyline"], [e.get("id") for e in events]
        )
        flat = pd.DataFrame(bettingpros.flatten_offers(offers))
    except Exception as e:
        log.warning("BettingPros moneylines unavailable: %s", e)
        return games
    if flat.empty:
        return games

    flat["norm_team"] = flat["participant"].map(normalize)
    best = flat.sort_values("odds", ascending=False).drop_duplicates("norm_team")
    prices = dict(zip(best["norm_team"], best["odds"]))

    games = games.copy()
    for side in ("home", "away"):
        games[f"{side}_ml"] = games[f"{side}_team"].map(lambda t: prices.get(normalize(t)))
        games[f"{side}_ev"] = games.apply(
            lambda r: round(
                odds.expected_value(r[f"{side}_win_prob"], r[f"{side}_ml"]), 4
            )
            if pd.notna(r[f"{side}_ml"])
            else None,
            axis=1,
        )
    return games


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(date: str | None = None) -> dict:
    date = date or _date.today().isoformat()
    games = project_games(date)
    games = attach_game_edges(games, date)
    props = project_props(date)
    props = attach_prop_edges(props, date)

    out = {
        "date": date,
        "generated_at": pd.Timestamp.utcnow().isoformat(),
        "games": games.to_dict(orient="records"),
        "props": props.to_dict(orient="records"),
    }
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = config.OUTPUT_DIR / "latest.json"
    path.write_text(json.dumps(out, indent=1, default=str))
    log.info("wrote %s (%d games, %d props)", path, len(games), len(props))
    return out
