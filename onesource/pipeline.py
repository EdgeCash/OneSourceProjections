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

from . import config, odds, parks
from .clients import bettingpros, espn, fantasypros, mlb_statsapi, statcast
from .models import game as game_model
from .models import generic
from .models import props as prop_model
from .names import normalize
from .sports import SPORTS, active_sports

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


def _fp_projections(season: int, date: str) -> dict[str, dict]:
    """Daily per-game projections keyed by normalized player name."""
    try:
        players = fantasypros.mlb_projections(season, proj_type="daily", date=date)
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
        # Park: game is at the home team's venue; de-bias each team's rate
        # by its own home park (see models/game.expected_runs).
        pf_venue = parks.factor(g["home_team"])
        home = game_model.TeamInputs(
            name=g["home_team"],
            runs_per_game=_team_runs_per_game(g["home_team_id"], date),
            opp_starter_xfip=starter_xfip(g["away_pitcher"]),
            park_factor=pf_venue,
            own_home_pf=pf_venue,
        )
        away = game_model.TeamInputs(
            name=g["away_team"],
            runs_per_game=_team_runs_per_game(g["away_team_id"], date),
            opp_starter_xfip=starter_xfip(g["home_pitcher"]),
            park_factor=pf_venue,
            own_home_pf=parks.factor(g["away_team"]),
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
    fp = _fp_projections(season, date)

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

        # FP daily projections are already per-game stat lines.
        fp_stats = fp.get(normalize(name), {})
        fp_k_today = _lookup_float(fp_stats, "K", "SO", "strikeouts")

        model = prop_model.pitcher_strikeouts(exp_innings, k_rate, opp_k, fp_k_today)
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
            # FP daily projections are already per-game stat lines.
            fp_stats = fp.get(normalize(name), {})
            fp_h = _lookup_float(fp_stats, "H", "hits")
            fp_hr = _lookup_float(fp_stats, "HR")
            fp_tb = _lookup_float(fp_stats, "TB")
            if fp_tb is None and fp_h is not None:
                d2 = _lookup_float(fp_stats, "2B") or 0
                d3 = _lookup_float(fp_stats, "3B") or 0
                fp_tb = fp_h + d2 + 2 * d3 + 3 * (fp_hr or 0)

            ba = _lookup_float(stats_row, "AVG")
            xba = _lookup_float(stats_row, "est_ba")
            hits = prop_model.batter_hits(exp_ab, ba, xba, fp_h)

            slg = _lookup_float(stats_row, "SLG")
            xslg = _lookup_float(stats_row, "est_slg")
            tb = prop_model.batter_total_bases(exp_ab, slg, xslg, fp_tb)

            pa_total = _lookup_float(stats_row, "PA")
            hr_total = _lookup_float(stats_row, "HR")
            hr_rate = hr_total / pa_total if hr_total and pa_total else None
            hr = prop_model.batter_home_run(exp_ab + 0.4, hr_rate, fp_hr)

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
                 "dist": "negbinom", "param": tb["lambda"]},
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
    if row["dist"] == "negbinom":
        return prop_model.prob_over_neg_binom(row["param"], line)
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
    merged = _attach_bp_consensus(merged, date)
    return merged.drop(columns=["norm_player"])


def _attach_bp_consensus(props: pd.DataFrame, date: str) -> pd.DataFrame:
    """Add BettingPros' own projection / EV / recommended side (premium
    fields via auth=user) as a second opinion next to our model."""
    prop_market_ids = [
        mid for name, mid in config.BP_MARKET_IDS.items()
        if name.startswith(("pitcher_", "batter_"))
    ]
    try:
        raw = bettingpros.props("MLB", date, prop_market_ids)
    except Exception as e:
        log.warning("BettingPros /props unavailable: %s", e)
        return props
    flat = pd.DataFrame(bettingpros.flatten_props(raw))
    if flat.empty or flat["participant"].isna().all():
        return props

    id_to_market = {mid: name for name, mid in config.BP_MARKET_IDS.items()}
    flat["market"] = flat["market_id"].map(id_to_market)
    flat["norm_player"] = flat["participant"].map(normalize)
    flat = flat.dropna(subset=["market"]).drop_duplicates(["norm_player", "market"])
    cols = ["norm_player", "market", "bp_projection", "bp_ev",
            "bp_recommended_side", "bp_bet_rating"]
    return props.merge(flat[cols], on=["norm_player", "market"], how="left")


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
# Generic sports (WNBA, NBA, NFL, NCAAF, NHL)
# ---------------------------------------------------------------------------

def project_generic_games(sport_key: str, date: str) -> pd.DataFrame:
    sport = SPORTS[sport_key]
    slate = espn.slate(sport_key, date)
    if not slate:
        return pd.DataFrame()
    start = mlb_statsapi._shift_date(date, -sport.form_days)
    try:
        results = espn.results_range(sport_key, start, date)
    except Exception as e:
        log.warning("%s results unavailable: %s", sport_key, e)
        results = []
    ratings = generic.team_ratings(results, sport.league_ppg)

    rows = []
    for g in slate:
        proj = generic.project_game(
            sport, ratings.get(g["home_team"]), ratings.get(g["away_team"])
        )
        rows.append(
            {
                "game_id": g["game_id"],
                "game_time": g["game_time"],
                "away_team": g["away_team"],
                "home_team": g["home_team"],
                "away_exp": proj.away_exp,
                "home_exp": proj.home_exp,
                "proj_total": proj.total_mean,
                "home_win_prob": proj.home_win_prob,
                "away_win_prob": round(1 - proj.home_win_prob, 4),
                "_proj": proj,
            }
        )
    return pd.DataFrame(rows)


def attach_generic_game_edges(games: pd.DataFrame, sport_key: str, date: str) -> pd.DataFrame:
    if games.empty:
        return games
    sport = SPORTS[sport_key]
    try:
        market_ids = bettingpros.game_market_ids(sport_key)
        events = bettingpros.events(sport_key, date)
        event_ids = [e.get("id") for e in events if e.get("id")]
    except Exception as e:
        log.warning("%s BettingPros unavailable: %s", sport_key, e)
        return games.drop(columns=["_proj"])

    flat_by_market: dict[str, pd.DataFrame] = {}
    for market, mid in market_ids.items():
        if mid is None:
            continue
        try:
            raw = bettingpros.offers(sport_key, mid, event_ids)
            df = pd.DataFrame(bettingpros.flatten_offers(raw))
            if not df.empty:
                df["norm_team"] = df["participant"].map(normalize)
                flat_by_market[market] = df[df["active"] & df["odds"].notna()]
        except Exception as e:
            log.warning("%s %s offers unavailable: %s", sport_key, market, e)

    games = games.copy()

    ml = flat_by_market.get("moneyline", pd.DataFrame())
    if not ml.empty:
        best = ml.sort_values("odds", ascending=False).drop_duplicates("norm_team")
        pairs = list(zip(best["norm_team"], best["odds"]))
        for side in ("home", "away"):
            games[f"{side}_ml"] = games[f"{side}_team"].map(
                lambda t: _best_price_for_team(t, pairs))
            games[f"{side}_ml_ev"] = games.apply(
                lambda r: round(odds.expected_value(
                    r[f"{side}_win_prob"], r[f"{side}_ml"]), 4)
                if pd.notna(r[f"{side}_ml"]) else None, axis=1)

    tot = flat_by_market.get("total", pd.DataFrame())
    if not tot.empty and "line" in tot.columns:
        event_teams = _bp_event_teams(events)
        overs = tot[tot["selection"].astype(str).str.lower().str.contains("over", na=False)]
        overs = overs[overs["line"].notna()]
        overs = overs.sort_values("odds", ascending=False).drop_duplicates("event_id")
        by_event = {r["event_id"]: r for _, r in overs.iterrows()}

        def total_cols(row):
            offer = None
            for eid, teams in event_teams.items():
                if eid in by_event and any(
                    _teams_match(row["home_team"], t) or _teams_match(row["away_team"], t)
                    for t in teams
                ):
                    offer = by_event[eid]
                    break
            if offer is None:
                return pd.Series({"total_line": None, "over_odds": None,
                                  "model_over_prob": None, "over_ev": None})
            line = float(offer["line"])
            p = row["_proj"].prob_over(line, sport)
            return pd.Series({
                "total_line": line,
                "over_odds": offer["odds"],
                "model_over_prob": round(p, 4),
                "over_ev": round(odds.expected_value(p, float(offer["odds"])), 4),
            })

        games = pd.concat([games, games.apply(total_cols, axis=1)], axis=1)

    return games.drop(columns=["_proj"])


def _teams_match(a: str | None, b: str | None) -> bool:
    """Loose match across sources: 'New York Yankees' vs 'Yankees' vs 'NYY'."""
    if not a or not b:
        return False
    na, nb = normalize(a), normalize(b)
    return na == nb or na in nb or nb in na or na.split()[-1] == nb.split()[-1]


def _best_price_for_team(team: str, pairs: list[tuple[str, float]]) -> float | None:
    matches = [odds_ for name, odds_ in pairs if _teams_match(team, name)]
    return max(matches) if matches else None


def _bp_event_teams(events: list[dict]) -> dict[int, list[str]]:
    """event_id -> team names, pulled defensively from the events payload."""
    out: dict[int, list[str]] = {}
    for e in events:
        eid = e.get("id")
        if eid is None:
            continue
        names = []
        participants = e.get("participants") or e.get("teams") or []
        for p in participants:
            if isinstance(p, dict):
                name = (p.get("name") or (p.get("team") or {}).get("name")
                        or p.get("id"))
                if name:
                    names.append(str(name))
            elif isinstance(p, str):
                names.append(p)
        for key in ("home", "away"):
            side = e.get(key)
            if isinstance(side, dict) and side.get("name"):
                names.append(side["name"])
            elif isinstance(side, str):
                names.append(side)
        out[int(eid)] = names
    return out


_FP_STAT_KEYWORDS = {
    "point": ("PTS", "points"),
    "rebound": ("REB", "rebounds"),
    "assist": ("AST", "assists"),
    "three": ("3PM", "THREES", "three_pointers"),
    "steal": ("STL", "steals"),
    "block": ("BLK", "blocks"),
}


def _fp_generic_index(sport_key: str, date: str) -> dict[str, dict]:
    """Daily FantasyPros projections for sports that have them (NBA)."""
    if SPORTS[sport_key].fp_projections != "daily" or sport_key == "MLB":
        return {}
    try:
        if sport_key == "NBA":
            players = fantasypros.nba_projections(_season(date), date)
            return fantasypros.projection_index(players)
    except Exception as e:
        log.warning("%s FantasyPros unavailable: %s", sport_key, e)
    return {}


def _fp_stat_for_market(fp_stats: dict, market_name: str) -> float | None:
    name = market_name.lower()
    for keyword, keys in _FP_STAT_KEYWORDS.items():
        if keyword in name:
            return _lookup_float(fp_stats, *keys)
    return None


def project_generic_props(sport_key: str, date: str) -> pd.DataFrame:
    """Props for non-MLB sports: BettingPros lines + premium projections,
    blended with FantasyPros where available, with our distribution on top."""
    try:
        raw = bettingpros.props(sport_key, date)
    except Exception as e:
        log.warning("%s BettingPros props unavailable: %s", sport_key, e)
        return pd.DataFrame()
    flat = pd.DataFrame(bettingpros.flatten_props(raw))
    if flat.empty or flat["participant"].isna().all():
        return pd.DataFrame()

    try:
        lookup = bettingpros.market_lookup(sport_key)
    except Exception:
        lookup = {}
    fp = _fp_generic_index(sport_key, date)

    rows = []
    for _, r in flat.iterrows():
        mid = r.get("market_id")
        market_name = lookup.get(int(mid), {}).get("name", str(mid)) if pd.notna(mid) else ""
        fp_stats = fp.get(normalize(r["participant"]), {}) if r["participant"] else {}
        fp_proj = _fp_stat_for_market(fp_stats, market_name) if fp_stats else None
        bp_proj = r.get("bp_projection")
        sources = [v for v in (fp_proj, bp_proj) if v is not None and pd.notna(v)]
        projection = sum(map(float, sources)) / len(sources) if sources else None

        line = r.get("over_line") if pd.notna(r.get("over_line")) else r.get("bp_line")
        row = {
            "market": market_name,
            "player": r["participant"],
            "projection": round(projection, 2) if projection is not None else None,
            "fp_projection": fp_proj,
            "bp_projection": bp_proj,
            "bp_ev": r.get("bp_ev"),
            "bp_recommended_side": r.get("bp_recommended_side"),
            "bp_bet_rating": r.get("bp_bet_rating"),
            "line": line,
            "over_odds": r.get("over_odds"),
            "under_odds": r.get("under_odds"),
            "model_over_prob": None, "ev_over": None, "ev_under": None, "kelly": None,
        }
        if projection is not None and pd.notna(line):
            p_over = generic.prop_prob_over(float(projection), float(line), market_name)
            row["model_over_prob"] = round(p_over, 4)
            if pd.notna(r.get("over_odds")):
                row["ev_over"] = round(odds.expected_value(p_over, float(r["over_odds"])), 4)
            if pd.notna(r.get("under_odds")):
                row["ev_under"] = round(
                    odds.expected_value(1 - p_over, float(r["under_odds"])), 4)
            best_ev = max(
                [v for v in (row["ev_over"], row["ev_under"]) if v is not None],
                default=None)
            if best_ev is not None and best_ev > 0:
                side_odds = (r["over_odds"] if best_ev == row["ev_over"]
                             else r["under_odds"])
                side_prob = p_over if best_ev == row["ev_over"] else 1 - p_over
                row["kelly"] = round(odds.kelly_stake(
                    side_prob, float(side_odds), config.KELLY_FRACTION), 4)
        rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def _run_mlb(date: str) -> dict:
    games = attach_game_edges(project_games(date), date)
    props = attach_prop_edges(project_props(date), date)
    return {"games": games.to_dict(orient="records"),
            "props": props.to_dict(orient="records")}


def _run_generic(sport_key: str, date: str) -> dict:
    games = attach_generic_game_edges(project_generic_games(sport_key, date),
                                      sport_key, date)
    props = project_generic_props(sport_key, date)
    return {"games": games.to_dict(orient="records"),
            "props": props.to_dict(orient="records")}


def run(date: str | None = None, sports: list[str] | None = None) -> dict:
    date = date or _date.today().isoformat()
    sports = sports or active_sports(date)

    out_sports = {}
    for key in sports:
        if key not in SPORTS:
            log.warning("unknown sport %s, skipping", key)
            continue
        try:
            out_sports[key] = _run_mlb(date) if key == "MLB" else _run_generic(key, date)
            log.info("%s: %d games, %d props", key,
                     len(out_sports[key]["games"]), len(out_sports[key]["props"]))
        except Exception as e:
            log.error("%s pipeline failed: %s", key, e)
            out_sports[key] = {"games": [], "props": [], "error": str(e)}

    out = {
        "date": date,
        "generated_at": pd.Timestamp.utcnow().isoformat(),
        "sports": out_sports,
    }
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = config.OUTPUT_DIR / "latest.json"
    path.write_text(json.dumps(out, indent=1, default=str))
    log.info("wrote %s", path)
    return out
