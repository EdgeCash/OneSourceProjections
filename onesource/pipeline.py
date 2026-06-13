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

from . import config, internal_stats, odds, parks, playerlogs, teams, weather
from .clients import bettingpros, espn, fantasypros, mlb_statsapi, statcast
from .models import game as game_model
from .models import generic
from .models import props as prop_model
from .models.elo import Elo
from .names import normalize
from .sports import SPORTS, active_sports

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Market evaluation: price sanity + de-vig + shrink toward market
# ---------------------------------------------------------------------------

def _market_eval(p_model_a: float, a_price, b_price) -> dict:
    """Evaluate a two-way market for both sides from one model probability.

    Rejects incoherent price pairs, shrinks the model probability toward the
    de-vigged market consensus, and returns shrunk EVs. Keeps the raw-price
    EV nowhere — only the blended, sanity-checked numbers reach the site.

    Returns {p_used, p_fair, ev_a, ev_b}; ev_* are None where that side has
    no usable price. ``p_used`` falls back to the raw model prob when there's
    no market to anchor to.
    """
    a_ok = a_price is not None and pd.notna(a_price)
    b_ok = b_price is not None and pd.notna(b_price)
    out = {"p_used": p_model_a, "p_fair": None, "ev_a": None, "ev_b": None}
    if a_ok and b_ok:
        # two-way market: de-vig to a clean consensus and shrink toward it
        fair = odds.fair_two_way(float(a_price), float(b_price),
                                 config.VIG_SUM_MIN, config.VIG_SUM_MAX)
        if fair is None:
            return out  # incoherent pair -> no edge either side
        p_fair = fair[0]
        p = odds.blend_toward_market(p_model_a, p_fair, config.MARKET_SHRINK)
        out["p_fair"] = round(p_fair, 4)
    elif a_ok or b_ok:
        # single quoted side: the lone implied prob still carries the book's
        # vig, so it is NOT a clean consensus — shrinking toward it would bias
        # the probability up and erase genuine edges (this silently gutted the
        # props board). Reject only implausible prices; trust the model prob.
        price = float(a_price if a_ok else b_price)
        if odds.fair_one_way(price) is None:
            return out  # implausible single price
        p = p_model_a
    else:
        return out
    out["p_used"] = round(p, 4)
    if a_ok:
        out["ev_a"] = round(odds.expected_value(p, float(a_price)), 4)
    if b_ok:
        out["ev_b"] = round(odds.expected_value(1 - p, float(b_price)), 4)
    return out


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
    """Starter rates from our own box logs (FanGraphs is blocked on CI
    runners, so internal data is primary; pybaseball remains a fallback)."""
    df = internal_stats.pitcher_table(season)
    if not df.empty:
        return df
    try:
        df = statcast.season_pitching(season)
        return df.assign(norm_name=df["Name"].map(normalize))
    except Exception as e:
        log.warning("pitching stats unavailable: %s", e)
        return pd.DataFrame(columns=["Name", "norm_name"])


def _batter_table(season: int) -> pd.DataFrame:
    """Batter rates from our own box logs + prior-season Statcast xstats."""
    df = internal_stats.batter_table(season)
    if not df.empty:
        return df
    try:
        df = statcast.season_batting(season)
        return df.assign(norm_name=df["Name"].map(normalize))
    except Exception as e:
        log.warning("batting stats unavailable: %s", e)
        return pd.DataFrame(columns=["Name", "norm_name"])


FP_HISTORY = config.REPO_ROOT / "data" / "history" / "fantasypros"


def _persist_fp(sport: str, date: str, players: list):
    """Keep every FantasyPros projection pull — projection-accuracy history
    is part of the library we're building."""
    if not players:
        return
    try:
        FP_HISTORY.mkdir(parents=True, exist_ok=True)
        (FP_HISTORY / f"{sport.lower()}_{date}.json").write_text(
            json.dumps(players, default=str))
    except Exception as e:
        log.warning("could not persist FP %s %s: %s", sport, date, e)


def _fp_projections(season: int, date: str) -> dict[str, dict]:
    """Daily per-game projections keyed by normalized player name."""
    try:
        players = fantasypros.mlb_projections(season, proj_type="daily", date=date)
        _persist_fp("mlb", date, players)
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
    bullpens = internal_stats.bullpen_fip(season)

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
            opp_bullpen_xfip=bullpens.get(teams.canon("MLB", g["away_team"])),
            park_factor=pf_venue,
            own_home_pf=pf_venue,
        )
        away = game_model.TeamInputs(
            name=g["away_team"],
            runs_per_game=_team_runs_per_game(g["away_team_id"], date),
            opp_starter_xfip=starter_xfip(g["home_pitcher"]),
            opp_bullpen_xfip=bullpens.get(teams.canon("MLB", g["home_team"])),
            park_factor=pf_venue,
            own_home_pf=parks.factor(g["away_team"]),
        )
        proj = game_model.simulate(home, away)
        try:
            wx = weather.game_weather(g["home_team"], g["game_time"])
        except Exception:
            wx = None
        try:
            lu = mlb_statsapi.batting_order(g["game_pk"])
            lineups = {s_: [p["name"] for p in lu.get(s_, [])]
                       for s_ in ("home", "away")} if lu else None
        except Exception:
            lineups = None
        rows.append(
            {
                "weather": wx,
                "lineups": lineups,
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


_PROP_EDGE_COLS = ("line", "odds", "book_id", "model_over_prob", "ev", "kelly",
                   "bp_projection", "bp_ev", "bp_probability",
                   "bp_recommended_side", "bp_bet_rating")


def _ensure_cols(df: pd.DataFrame, cols=_PROP_EDGE_COLS) -> pd.DataFrame:
    """Guarantee the market columns exist (as None) so downstream display
    code never KeyErrors when offers were unavailable."""
    for c in cols:
        if c not in df.columns:
            df[c] = None
    return df


def attach_prop_edges(props: pd.DataFrame, date: str) -> pd.DataFrame:
    """Join model projections to BettingPros prop offers and compute EV."""
    if props.empty:
        return props
    try:
        events = bettingpros.events("MLB", date)
        event_ids = [e.get("id") for e in events if e.get("id")]
    except Exception as e:
        log.warning("BettingPros events unavailable: %s", e)
        return _ensure_cols(props.copy())

    try:
        live_ids = bettingpros.prop_market_ids("MLB")
    except Exception as e:
        log.warning("prop market resolution failed: %s", e)
        live_ids = {}

    market_rows = []
    for market, mid in live_ids.items():
        try:
            offers = bettingpros.offers("MLB", mid, event_ids,
                                        season=_season(date))
            for r in bettingpros.flatten_offers(offers):
                r["market"] = market
                market_rows.append(r)
        except Exception as e:
            log.warning("offers for %s unavailable: %s", market, e)

    if not market_rows:
        return _ensure_cols(_attach_bp_consensus_keyed(props.copy(), date))

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
        ev = _market_eval(p, row["odds"], None)
        k = (round(odds.kelly_stake(ev["p_used"], float(row["odds"]),
                                    config.KELLY_FRACTION), 4)
             if ev["ev_a"] is not None else None)
        return pd.Series({"model_over_prob": round(p, 4), "ev": ev["ev_a"],
                          "kelly": k})

    merged = pd.concat([merged, merged.apply(compute, axis=1)], axis=1)
    merged = _attach_bp_consensus_keyed(merged, date)
    return _ensure_cols(merged.drop(columns=["norm_player"], errors="ignore"))


def _attach_bp_consensus_keyed(props: pd.DataFrame, date: str) -> pd.DataFrame:
    """Add BettingPros' own projection / EV / recommended side (premium
    fields) as a second opinion next to our model. Also backfills the prop
    line from BP's board when no offers were available, so hit-rate splits
    and model probabilities can still be computed against a real number."""
    try:
        raw = bettingpros.props("MLB", date)
        live_ids = bettingpros.prop_market_ids("MLB")
    except Exception as e:
        log.warning("BettingPros /props unavailable: %s", e)
        return props
    flat = pd.DataFrame(bettingpros.flatten_props(raw))
    if flat.empty or flat["participant"].isna().all():
        return props

    id_to_market = {mid: name for name, mid in live_ids.items()}
    flat["market"] = flat["market_id"].map(id_to_market)
    flat["norm_player"] = flat["participant"].map(normalize)
    flat = flat.dropna(subset=["market"]).drop_duplicates(["norm_player", "market"])
    cols = ["norm_player", "market", "bp_line", "bp_projection", "bp_ev",
            "bp_probability", "bp_recommended_side", "bp_bet_rating",
            "over_odds", "under_odds"]
    cols = [c for c in cols if c in flat.columns]
    if "norm_player" not in props.columns:
        props = props.assign(norm_player=props["player"].map(normalize))
    merged = props.merge(flat[cols], on=["norm_player", "market"], how="left")
    # backfill line + price from BP's board, then (re)compute our model prob
    if "line" not in merged.columns:
        merged["line"] = None
    merged["line"] = merged["line"].where(merged["line"].notna(),
                                          merged.get("bp_line"))
    if "odds" not in merged.columns:
        merged["odds"] = None
    merged["odds"] = merged["odds"].where(merged["odds"].notna(),
                                          merged.get("over_odds"))

    def compute(row):
        out = {"model_over_prob": row.get("model_over_prob"),
               "ev": row.get("ev"), "kelly": row.get("kelly")}
        if out["model_over_prob"] is None or pd.isna(out["model_over_prob"]):
            line = row.get("line")
            if line is not None and pd.notna(line):
                p = prob_over_for_row(row, float(line))
                if p is not None:
                    out["model_over_prob"] = round(p, 4)
                    price = row.get("odds")
                    if price is not None and pd.notna(price):
                        ev = _market_eval(p, price, None)
                        out["ev"] = ev["ev_a"]
                        if ev["ev_a"] is not None:
                            out["kelly"] = round(odds.kelly_stake(
                                ev["p_used"], float(price), config.KELLY_FRACTION), 4)
        return pd.Series(out)

    recomputed = merged.apply(compute, axis=1)
    for c in recomputed.columns:
        merged[c] = recomputed[c]
    return merged


def attach_game_edges(games: pd.DataFrame, date: str) -> pd.DataFrame:
    """Join moneyline / total / run-line offers to MLB game projections and
    compute EV. Market ids resolve at runtime; team matching is loose
    (BettingPros may use nicknames or abbreviations)."""
    from scipy import stats as _st

    games = games.copy()
    game_cols = ("home_ml", "away_ml", "home_ml_ev", "away_ml_ev",
                 "total_line", "over_odds", "under_odds", "model_over_prob",
                 "over_ev", "under_ev", "rl_home_line", "rl_home_odds",
                 "rl_away_odds", "model_home_rl", "rl_home_ev", "rl_away_ev")
    _ensure_cols(games, game_cols)
    if games.empty:
        return games
    try:
        market_ids = bettingpros.game_market_ids("MLB")
        events = bettingpros.events("MLB", date)
        event_ids = [e.get("id") for e in events if e.get("id")]
    except Exception as e:
        log.warning("BettingPros game markets unavailable: %s", e)
        return games

    flat_by_market: dict[str, pd.DataFrame] = {}
    for market, mid in market_ids.items():
        if mid is None:
            continue
        try:
            raw = bettingpros.offers("MLB", mid, event_ids, season=_season(date))
            df = pd.DataFrame(bettingpros.flatten_offers(raw))
            if not df.empty:
                flat_by_market[market] = df[df["active"] & df["odds"].notna()]
        except Exception as e:
            log.warning("MLB %s offers unavailable: %s", market, e)

    # event -> team names, from the events payload plus the offers' own
    # participants (the offers always carry them, the events shape varies)
    event_teams = dict(_bp_event_teams(events))
    for df in flat_by_market.values():
        if df is None or df.empty or "event_id" not in df.columns:
            continue
        for eid, grp in df.groupby("event_id"):
            names = set(event_teams.get(eid, []))
            names |= set(grp["participant"].dropna().astype(str))
            event_teams[eid] = list(names)

    def event_offer(df, row, side_label=None):
        """Rows of df belonging to this game (via BP event team names)."""
        if df is None or df.empty:
            return df
        ids = [eid for eid, ts in event_teams.items()
               if any(_teams_match(row["home_team"], t)
                      or _teams_match(row["away_team"], t) for t in ts)]
        return df[df["event_id"].isin(ids)] if ids else df.iloc[0:0]

    ml = flat_by_market.get("moneyline", pd.DataFrame())
    tot = flat_by_market.get("total", pd.DataFrame())
    rl = flat_by_market.get("spread", pd.DataFrame())

    def per_game(row):
        out = {}
        # moneyline: best price per side by team-name match within the event
        mlg = event_offer(ml, row)
        if mlg is not None and not mlg.empty:
            prices = {}
            for side in ("home", "away"):
                m = mlg[mlg["participant"].map(
                    lambda p: _teams_match(row[f"{side}_team"], p or ""))]
                if not m.empty:
                    prices[side] = float(m["odds"].max())
                    out[f"{side}_ml"] = prices[side]
            ev = _market_eval(row["home_win_prob"], prices.get("home"),
                              prices.get("away"))
            if ev["ev_a"] is not None:
                out["home_ml_ev"] = ev["ev_a"]
            if ev["ev_b"] is not None:
                out["away_ml_ev"] = ev["ev_b"]
        # total
        tg = event_offer(tot, row)
        if tg is not None and not tg.empty and "line" in tg.columns:
            overs = tg[tg["selection"].astype(str).str.lower()
                       .str.contains("over", na=False) & tg["line"].notna()]
            unders = tg[tg["selection"].astype(str).str.lower()
                        .str.contains("under", na=False) & tg["line"].notna()]
            if not overs.empty:
                best_o = overs.sort_values("odds", ascending=False).iloc[0]
                line = float(best_o["line"])
                over_probs = row.get("over_probs") or {}
                p = over_probs.get(line, over_probs.get(str(line)))
                if p is None:
                    p = float(1 - _st.poisson.cdf(int(line), row["proj_total"]))
                out.update({"total_line": line, "over_odds": float(best_o["odds"]),
                            "model_over_prob": round(float(p), 4)})
                under_price = None
                if not unders.empty:
                    best_u = unders.sort_values("odds", ascending=False).iloc[0]
                    under_price = float(best_u["odds"])
                    out["under_odds"] = under_price
                ev = _market_eval(float(p), float(best_o["odds"]), under_price)
                if ev["ev_a"] is not None:
                    out["over_ev"] = ev["ev_a"]
                if ev["ev_b"] is not None:
                    out["under_ev"] = ev["ev_b"]
        # run line (home -1.5 / +1.5)
        rg = event_offer(rl, row)
        if rg is not None and not rg.empty and "line" in rg.columns:
            cover = row.get("home_rl_cover") or {}
            home_rows = rg[rg["participant"].map(
                lambda p: _teams_match(row["home_team"], p or ""))
                & rg["line"].notna()]
            away_rows = rg[rg["participant"].map(
                lambda p: _teams_match(row["away_team"], p or ""))
                & rg["line"].notna()]
            if not home_rows.empty:
                best_h = home_rows.sort_values("odds", ascending=False).iloc[0]
                spread = float(best_h["line"])
                p_cover = cover.get(spread, cover.get(str(spread)))
                out["rl_home_line"] = spread
                out["rl_home_odds"] = float(best_h["odds"])
                away_price = None
                if not away_rows.empty:
                    best_a = away_rows.sort_values("odds", ascending=False).iloc[0]
                    away_price = float(best_a["odds"])
                    out["rl_away_odds"] = away_price
                if p_cover is not None:
                    out["model_home_rl"] = round(float(p_cover), 4)
                    ev = _market_eval(float(p_cover), float(best_h["odds"]), away_price)
                    if ev["ev_a"] is not None:
                        out["rl_home_ev"] = ev["ev_a"]
                    if ev["ev_b"] is not None:
                        out["rl_away_ev"] = ev["ev_b"]
        return pd.Series(out, dtype=object)

    extra = games.apply(per_game, axis=1)
    for c in extra.columns:
        games[c] = extra[c]
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

    # Elo: maintain ratings over a longer history (covers prior + current
    # season for cross-season carryover), then blend its win prob in.
    elo = None
    if sport.elo_blend > 0:
        elo = Elo()
        try:
            elo_results = espn.results_range(
                sport_key, mlb_statsapi._shift_date(date, -500), date)
            for r in sorted(elo_results, key=lambda x: x["date"]):
                elo.update(r["home_team"], r["away_team"],
                           r["home_score"], r["away_score"], int(r["date"][:4]))
        except Exception as e:
            log.warning("%s Elo history unavailable: %s", sport_key, e)
            elo = None

    rows = []
    for g in slate:
        proj = generic.project_game(
            sport, ratings.get(g["home_team"]), ratings.get(g["away_team"])
        )
        hwp = proj.home_win_prob
        if elo is not None:
            season = int(str(g.get("date") or date)[:4])
            ewp = elo.home_win_prob(g["home_team"], g["away_team"], season)
            hwp = round((1 - sport.elo_blend) * hwp + sport.elo_blend * ewp, 4)
        rows.append(
            {
                "game_id": g["game_id"],
                "game_time": g["game_time"],
                "away_team": g["away_team"],
                "home_team": g["home_team"],
                "away_exp": proj.away_exp,
                "home_exp": proj.home_exp,
                "proj_total": proj.total_mean,
                "home_win_prob": hwp,
                "away_win_prob": round(1 - hwp, 4),
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
            raw = bettingpros.offers(sport_key, mid, event_ids,
                                     season=_season(date))
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

        def _ml_ev(r):
            ev = _market_eval(r["home_win_prob"], r.get("home_ml"), r.get("away_ml"))
            return pd.Series({"home_ml_ev": ev["ev_a"], "away_ml_ev": ev["ev_b"]})

        games = pd.concat([games, games.apply(_ml_ev, axis=1)], axis=1)

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
            ev = _market_eval(p, float(offer["odds"]), None)
            return pd.Series({
                "total_line": line,
                "over_odds": offer["odds"],
                "model_over_prob": round(p, 4),
                "over_ev": ev["ev_a"],
            })

        games = pd.concat([games, games.apply(total_cols, axis=1)], axis=1)

    # spread (point spread / run line equivalent for generic sports)
    sp = flat_by_market.get("spread", pd.DataFrame())
    if not sp.empty and "line" in sp.columns:
        event_teams = _bp_event_teams(events)

        def spread_cols(row):
            out = {"spread_home_line": None, "spread_home_odds": None,
                   "spread_away_odds": None, "model_home_cover": None,
                   "spread_home_ev": None, "spread_away_ev": None}
            ids = [eid for eid, ts in event_teams.items()
                   if any(_teams_match(row["home_team"], t)
                          or _teams_match(row["away_team"], t) for t in ts)]
            g = sp[sp["event_id"].isin(ids)] if ids else sp.iloc[0:0]
            home_rows = g[g["participant"].map(
                lambda p: _teams_match(row["home_team"], p or ""))
                & g["line"].notna()]
            if home_rows.empty:
                return pd.Series(out)
            best_h = home_rows.sort_values("odds", ascending=False).iloc[0]
            spread = float(best_h["line"])
            p = row["_proj"].home_cover_prob(spread, sport)
            away_rows = g[g["participant"].map(
                lambda p_: _teams_match(row["away_team"], p_ or ""))
                & g["line"].notna()]
            away_price = (float(away_rows.sort_values("odds", ascending=False)
                                .iloc[0]["odds"]) if not away_rows.empty else None)
            ev = _market_eval(p, float(best_h["odds"]), away_price)
            out.update({"spread_home_line": spread,
                        "spread_home_odds": float(best_h["odds"]),
                        "model_home_cover": round(p, 4),
                        "spread_home_ev": ev["ev_a"]})
            if away_price is not None:
                out["spread_away_odds"] = away_price
                out["spread_away_ev"] = ev["ev_b"]
            return pd.Series(out)

        games = pd.concat([games, games.apply(spread_cols, axis=1)], axis=1)

    _ensure_cols(games, ("home_ml", "away_ml", "home_ml_ev", "away_ml_ev",
                         "total_line", "over_odds", "model_over_prob",
                         "over_ev", "spread_home_line", "spread_home_odds",
                         "spread_away_odds", "model_home_cover",
                         "spread_home_ev", "spread_away_ev"))
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
        if not raw:
            # some sports return an empty board without a market filter
            # (observed live for WNBA) — retry with explicit market ids
            ids = list(bettingpros.prop_market_ids(sport_key).values())
            if ids:
                raw = bettingpros.props(sport_key, date, market_ids=ids)
                log.info("%s props retry with market ids %s -> %d rows",
                         sport_key, ids, len(raw))
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
        info = lookup.get(int(mid), {}) if pd.notna(mid) else {}
        market_name = (info.get("name") or info.get("slug", "").replace("-", " ").title()
                       or (f"Market {int(mid)}" if pd.notna(mid) else ""))
        fp_stats = fp.get(normalize(r["participant"]), {}) if r["participant"] else {}
        fp_proj = _fp_stat_for_market(fp_stats, market_name) if fp_stats else None
        bp_proj = r.get("bp_projection")
        if isinstance(bp_proj, dict):  # early-format snapshot rows
            bp_proj = bp_proj.get("value")
        sources = [float(v) for v in (fp_proj, bp_proj)
                   if isinstance(v, (int, float)) and pd.notna(v)]
        projection = sum(sources) / len(sources) if sources else None

        line = r.get("over_line") if pd.notna(r.get("over_line")) else r.get("bp_line")
        row = {
            "market": market_name,
            "player": r["participant"],
            "team": r.get("player_team"),
            "position": r.get("player_position"),
            "player_image": r.get("player_image"),
            "projection": round(projection, 2) if projection is not None else None,
            "fp_projection": fp_proj,
            "bp_projection": bp_proj,
            "bp_ev": r.get("bp_ev"),
            "bp_probability": r.get("bp_probability"),
            "bp_recommended_side": r.get("bp_recommended_side"),
            "bp_bet_rating": r.get("bp_bet_rating"),
            "line": line,
            "over_odds": r.get("over_odds"),
            "under_odds": r.get("under_odds"),
            "over_open": r.get("over_open"),
            "over_consensus": r.get("over_consensus"),
            "opp_rank": r.get("opp_rank"),
            "pick_pct_over": r.get("pick_pct_over"),
            "picks_total": r.get("picks_total"),
            "streak": r.get("streak"),
            "streak_type": r.get("streak_type"),
            "perf_l5": r.get("perf_l5"), "perf_l10": r.get("perf_l10"),
            "perf_l20": r.get("perf_l20"), "perf_season": r.get("perf_season"),
            "perf_h2h": r.get("perf_h2h"),
            "model_over_prob": None, "ev_over": None, "ev_under": None, "kelly": None,
        }
        if projection is not None and pd.notna(line):
            p_over = generic.prop_prob_over(float(projection), float(line), market_name)
            row["model_over_prob"] = round(p_over, 4)
            ev = _market_eval(p_over, r.get("over_odds"), r.get("under_odds"))
            row["ev_over"], row["ev_under"] = ev["ev_a"], ev["ev_b"]
            best_ev = max(
                [v for v in (row["ev_over"], row["ev_under"]) if v is not None],
                default=None)
            if best_ev is not None and best_ev > 0:
                over_side = best_ev == row["ev_over"]
                side_odds = r["over_odds"] if over_side else r["under_odds"]
                side_prob = ev["p_used"] if over_side else 1 - ev["p_used"]
                row["kelly"] = round(odds.kelly_stake(
                    side_prob, float(side_odds), config.KELLY_FRACTION), 4)
        rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def attach_hit_rates(props: pd.DataFrame, sport: str, date: str) -> pd.DataFrame:
    """Add L5/L10/L20/season/H2H over-rates (vs each prop's line) for the
    heatmap board: our own game logs first, BettingPros' performance
    records (perf_*) as fallback where our logs are thin."""
    if props.empty:
        return props
    season = int(date[:4])
    props = props.copy()
    recs = []
    for _, r in props.iterrows():
        try:
            recs.append(playerlogs.hit_rates(
                sport, r.get("player", ""), r.get("market", ""), r.get("line"),
                opponent=r.get("opponent"), season=season))
        except Exception:
            recs.append({})
    hr = pd.DataFrame(recs, index=props.index)
    for col in ("l5", "l10", "l20", "season", "h2h"):
        ours = hr[col] if col in hr.columns else pd.Series(None, index=props.index)
        bp = props[f"perf_{col}"] if f"perf_{col}" in props.columns else pd.Series(
            None, index=props.index)
        props[f"hr_{col}"] = ours.where(ours.notna(), bp)
    return props


_FP_NEWS_SPORTS = {"MLB", "NBA", "NHL", "NFL"}


def _sport_news(sport: str) -> list[dict]:
    if sport not in _FP_NEWS_SPORTS:
        return []
    try:
        items = fantasypros.news(sport, limit=15)
        _persist_fp(f"{sport.lower()}_news", _date.today().isoformat(), items)
        out = []
        for it in items[:15]:
            out.append({
                "title": it.get("headline") or it.get("title") or "",
                "body": (it.get("description") or it.get("body") or "")[:280],
                "player": it.get("player_name") or it.get("player") or "",
                "when": it.get("created") or it.get("updated") or "",
            })
        return out
    except Exception as e:
        log.warning("%s news unavailable: %s", sport, e)
        return []


def _sport_injuries(sport: str) -> list[dict]:
    if sport not in _FP_NEWS_SPORTS:
        return []
    try:
        rows = fantasypros.injuries(sport)
        out = []
        for r in rows[:200]:
            name = r.get("player_name") or r.get("name") or ""
            out.append({
                "player": name,
                "norm": normalize(name),
                "team": r.get("team") or r.get("team_id") or "",
                "status": r.get("status") or r.get("injury_status") or "",
                "note": (r.get("injury") or r.get("note") or
                         r.get("description") or "")[:120],
            })
        return [r for r in out if r["player"]]
    except Exception as e:
        log.warning("%s injuries unavailable: %s", sport, e)
        return []


def _run_mlb(date: str) -> dict:
    games = attach_game_edges(project_games(date), date)
    props = attach_hit_rates(attach_prop_edges(project_props(date), date), "MLB", date)
    return {"games": games.to_dict(orient="records"),
            "props": props.to_dict(orient="records")}


def _run_generic(sport_key: str, date: str) -> dict:
    games = attach_generic_game_edges(project_generic_games(sport_key, date),
                                      sport_key, date)
    props = attach_hit_rates(project_generic_props(sport_key, date), sport_key, date)
    return {"games": games.to_dict(orient="records"),
            "props": props.to_dict(orient="records")}


def run(date: str | None = None, sports: list[str] | None = None,
        write: bool = True) -> dict:
    date = date or _date.today().isoformat()
    sports = sports or active_sports(date)

    out_sports = {}
    for key in sports:
        if key not in SPORTS:
            log.warning("unknown sport %s, skipping", key)
            continue
        try:
            out_sports[key] = _run_mlb(date) if key == "MLB" else _run_generic(key, date)
            out_sports[key]["news"] = _sport_news(key)
            out_sports[key]["injuries"] = _sport_injuries(key)
            log.info("%s: %d games, %d props", key,
                     len(out_sports[key]["games"]), len(out_sports[key]["props"]))
        except Exception as e:
            log.error("%s pipeline failed: %s", key, e)
            out_sports[key] = {"games": [], "props": [], "error": str(e)}

    out = {
        "date": date,
        "generated_at": pd.Timestamp.now("UTC").isoformat(),
        "sports": out_sports,
    }
    if write:
        config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        path = config.OUTPUT_DIR / "latest.json"
        path.write_text(json.dumps(out, indent=1, default=str))
        log.info("wrote %s", path)
    return out
