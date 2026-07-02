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
from functools import lru_cache

import pandas as pd

from . import (config, internal_stats, odds, parks, platoon, playerlogs, teams,
               umpires, weather)
from .clients import bettingpros, espn, fantasypros, mlb_statsapi, statcast
from .models import game as game_model
from .models import generic
from .models import nba_props
from .models import nhl_props
from .models import props as prop_model
from .models import soccer
from .models import tennis
from .models import wnba_props
from .models.elo import Elo, EloConfig
from .names import normalize
from .sports import SPORTS, active_sports

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Market evaluation: price sanity + de-vig + shrink toward market
# ---------------------------------------------------------------------------

def _market_eval(p_model_a: float, a_price, b_price,
                 shrink: float | None = None) -> dict:
    """Evaluate a two-way market for both sides from one model probability.

    Rejects incoherent price pairs, shrinks the model probability toward the
    de-vigged market consensus, and returns shrunk EVs. Keeps the raw-price
    EV nowhere — only the blended, sanity-checked numbers reach the site.

    ``shrink`` is the weight on the market consensus vs the model (per-sport,
    via ``Sport.market_shrink``); falls back to the global ``config.MARKET_SHRINK``
    when unset. Only the two-way branch uses it — a single quoted side is never
    shrunk (a lone vig-inclusive price is not a clean consensus).

    Returns {p_used, p_fair, ev_a, ev_b}; ev_* are None where that side has
    no usable price. ``p_used`` falls back to the raw model prob when there's
    no market to anchor to.
    """
    shrink = config.MARKET_SHRINK if shrink is None else shrink
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
        p = odds.blend_toward_market(p_model_a, p_fair, shrink)
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


def _nb_prob_over(line_int: int, mean: float) -> float:
    """P(total > line_int) under a negative binomial matching the game sim's run
    dispersion: mean ``mean``, variance ``mean × RUN_DISPERSION``. This mirrors
    the distribution ``game.draw_runs`` samples from, so an off-grid total line is
    priced consistently with the simulation instead of from a thinner Poisson.
    Falls back to Poisson when dispersion ≤ 1 (the sim does the same)."""
    from scipy import stats as _st

    d = config.RUN_DISPERSION
    if d <= 1.0 or mean <= 0:
        return float(1 - _st.poisson.cdf(line_int, mean))
    p = 1.0 / d                 # var/mean = 1/p  ->  p = 1/d
    n = mean / (d - 1.0)        # mean = n(1-p)/p ->  n = mean/(d-1)
    return float(1 - _st.nbinom.cdf(line_int, n, p))


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------

def _season(date: str) -> int:
    return int(date[:4])


def _team_runs_per_game(team_id: int, date: str) -> float:
    """Recent runs-scored rate, regressed toward the league mean exactly the way
    the backtested model does (``generic._raw``): weight on the observed rate
    ramps with sample size (``RATING_SHRINK × min(1, n/10)``) and the rest is the
    league prior. The live path previously returned the *raw* last-30 mean with
    no such shrinkage, so production projected off a less-regressed offense than
    the model that was actually calibrated — over-weighting hot/cold streaks and
    teams that happened to face soft pitching. expected_runs applies its own
    TEAM_RATE_WEIGHT blend on top, matching the backtest's two-stage regression."""
    results = mlb_statsapi.team_recent_results(team_id, date, config.TEAM_FORM_GAMES)
    scored = [r["runs_scored"] for r in results if r["runs_scored"] is not None]
    if len(scored) < 5:
        return config.LEAGUE_RUNS_PER_GAME
    n = len(scored)
    w = generic.RATING_SHRINK * min(1.0, n / 10.0)
    return w * (sum(scored) / n) + (1 - w) * config.LEAGUE_RUNS_PER_GAME


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

# League batting anchors for the top-3 wOBA proxy (we store AVG/SLG, not wOBA).
_LG_AVG, _LG_SLG = 0.248, 0.405


def _batter_woba_map(batters: pd.DataFrame) -> dict:
    """{norm_name: pseudo-wOBA} from AVG/SLG (run-production proxy for the top-3
    first-inning signal; we don't store true wOBA)."""
    from .models import nrfi as _nrfi
    out: dict = {}
    if batters is None or batters.empty:
        return out
    for _, b in batters.iterrows():
        nm = b.get("norm_name")
        avg, slg = _lookup_float(b.to_dict(), "AVG"), _lookup_float(b.to_dict(), "SLG")
        if not nm or (avg is None and slg is None):
            continue
        a = (avg / _LG_AVG) if avg else 1.0
        s = (slg / _LG_SLG) if slg else 1.0
        out[nm] = _nrfi.LEAGUE_WOBA * (0.45 * a + 0.55 * s)
    return out


def _nrfi_game_fields(g: dict, fi_rates: dict, bq: dict, lineups: dict | None,
                      starter_xfip_fn, park: float) -> dict:
    """Forward P(NRFI/YRFI) for one game: log5 team first-inning rates refined
    by the specific starter (xFIP) and confirmed top-3 hitters (wOBA proxy)."""
    from .models import nrfi as _nrfi
    rh = fi_rates.get(teams.canon("MLB", g.get("home_team", "")))
    ra = fi_rates.get(teams.canon("MLB", g.get("away_team", "")))
    if not rh or not ra:
        return {}

    def top3(side):
        names = ((lineups or {}).get(side) or [])[:3]
        ws = [bq[normalize(n)] for n in names if normalize(n) in bq]
        return sum(ws) / len(ws) if ws else None

    r = _nrfi.yrfi_prob(
        off_away=ra["off"], allow_home=rh["allow"],
        off_home=rh["off"], allow_away=ra["allow"], park=park or 1.0,
        home_starter=starter_xfip_fn(g.get("home_pitcher")),
        away_starter=starter_xfip_fn(g.get("away_pitcher")),
        away_top3_woba=top3("away"), home_top3_woba=top3("home"))
    return {"model_nrfi_prob": r["p_nrfi"], "model_yrfi_prob": r["p_yrfi"],
            "nrfi_p_top": r["p_top"], "nrfi_p_bot": r["p_bot"]}


def project_games(date: str) -> pd.DataFrame:
    season = _season(date)
    slate = mlb_statsapi.schedule(date)
    pitchers = _pitcher_table(season)
    bullpens = internal_stats.bullpen_fip(season)
    # NRFI inputs: as-of first-inning team rates + a top-3 wOBA proxy
    from . import teamstats as _ts
    from .models import nrfi as _nrfi
    try:
        fi_rates = _nrfi.team_first_inning_rates(
            _ts.team_games("MLB", (season - 1, season)), asof=date)
    except Exception:
        fi_rates = {}
    bq = _batter_woba_map(_batter_table(season))

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
        # Weather first — its temperature feeds expected_runs (warm air carries
        # the ball). game_weather returns None for domes/unavailable forecasts,
        # so temp_f stays None there and the model skips the adjustment.
        try:
            wx = weather.game_weather(g["home_team"], g["game_time"])
        except Exception:
            wx = None
        game_temp = (wx or {}).get("temp_f")
        # Home-plate umpire (assignment + tendency). Best-effort: None until the
        # crew posts a few hours pre-game, and a neutral factor otherwise.
        try:
            ump = umpires.for_game(g["game_pk"])
        except Exception:
            ump = None
        ump_rf = (ump or {}).get("runs_factor") or 1.0
        home = game_model.TeamInputs(
            name=g["home_team"],
            runs_per_game=_team_runs_per_game(g["home_team_id"], date),
            opp_starter_xfip=starter_xfip(g["away_pitcher"]),
            opp_bullpen_xfip=bullpens.get(teams.canon("MLB", g["away_team"])),
            park_factor=pf_venue,
            own_home_pf=pf_venue,
            temp_f=game_temp,
            ump_runs_factor=ump_rf,
        )
        away = game_model.TeamInputs(
            name=g["away_team"],
            runs_per_game=_team_runs_per_game(g["away_team_id"], date),
            opp_starter_xfip=starter_xfip(g["home_pitcher"]),
            opp_bullpen_xfip=bullpens.get(teams.canon("MLB", g["home_team"])),
            park_factor=pf_venue,
            own_home_pf=parks.factor(g["away_team"]),
            temp_f=game_temp,
            ump_runs_factor=ump_rf,
        )
        proj = game_model.simulate(home, away)
        try:
            lu = mlb_statsapi.batting_order(g["game_pk"])
            lineups = {s_: [p["name"] for p in lu.get(s_, [])]
                       for s_ in ("home", "away")} if lu else None
            pids = {}
            for s_ in ("home", "away"):
                for p in (lu or {}).get(s_, []):
                    if p.get("name") and p.get("player_id"):
                        pids[normalize(p["name"])] = p["player_id"]
        except Exception:
            lineups, pids = None, {}
        for side in ("home", "away"):
            nm, pid = g.get(f"{side}_pitcher"), g.get(f"{side}_pitcher_id")
            if nm and pid:
                pids[normalize(nm)] = pid
        nrfi_fields = _nrfi_game_fields(g, fi_rates, bq, lineups,
                                        starter_xfip, pf_venue)
        rows.append(
            {
                "weather": wx,
                "umpire": ump,
                "lineups": lineups,
                "player_ids": pids or None,
                "game_pk": g["game_pk"],
                "game_time": g["game_time"],
                "away_team": g["away_team"],
                "home_team": g["home_team"],
                "away_pitcher": g["away_pitcher"],
                "home_pitcher": g["home_pitcher"],
                "away_pitcher_id": g.get("away_pitcher_id"),
                "home_pitcher_id": g.get("home_pitcher_id"),
                "home_exp_runs": proj.home_exp_runs,
                "away_exp_runs": proj.away_exp_runs,
                "proj_total": proj.total_mean,
                "home_win_prob": round(proj.home_win_prob, 4),
                "away_win_prob": round(1 - proj.home_win_prob, 4),
                "over_probs": proj.over_probs,
                "home_rl_cover": proj.home_runline_cover,
                **nrfi_fields,
            }
        )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Prop projections
# ---------------------------------------------------------------------------

def project_props(date: str) -> pd.DataFrame:
    season = _season(date)
    slate = mlb_statsapi.schedule(date)
    # Resolve the slate's starters' handedness live (by id) from the people
    # endpoint so the platoon nudge uses authoritative hands, not the stale map.
    try:
        platoon.prime_hands((g.get(f"{s}_pitcher_id"), g.get(f"{s}_pitcher"))
                            for g in slate for s in ("home", "away"))
    except Exception:
        pass
    pitchers = _pitcher_table(season)
    batters = _batter_table(season)
    fp = _fp_projections(season, date)

    rows: list[dict] = []
    for g in slate:
        rows += _pitcher_prop_rows(g, pitchers, fp, season)
        rows += _batter_prop_rows(g, batters, fp, season)
    return pd.DataFrame(rows)


def _pitcher_prop_rows(g: dict, pitchers: pd.DataFrame, fp: dict, season: int) -> list[dict]:
    rows = []
    # Home-plate umpire strikeout-zone tendency (shrunk + clamped; 1.0 when the
    # crew isn't posted or the weight is off). Applied to the K prop only.
    try:
        ump_k = (umpires.for_game(g["game_pk"]) or {}).get("k_factor") or 1.0
    except Exception:
        ump_k = 1.0
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
        # Recent pitch counts refine the season-average innings: a starter
        # building up / on a limit throws fewer than his season line implies.
        pid = g.get(f"{side}_pitcher_id")
        workload = None
        if pid:
            try:
                workload = mlb_statsapi.pitcher_recent_workload(pid, season)
            except Exception:
                workload = None
        exp_innings = prop_model.refine_expected_innings(exp_innings, workload)

        opp_team_id = g[f"{opp_side}_team_id"]
        opp_stats = mlb_statsapi.team_season_hitting(opp_team_id, season)
        opp_k = None
        so, pa = _lookup_float(opp_stats, "strikeOuts"), _lookup_float(opp_stats, "plateAppearances")
        if so and pa:
            opp_k = so / pa

        # FP daily projections are already per-game stat lines.
        fp_stats = fp.get(normalize(name), {})
        fp_k_today = _lookup_float(fp_stats, "K", "SO", "strikeouts")

        common = {
            "game_pk": g["game_pk"],
            "player": name,
            "team": g[f"{side}_team"],
            "opponent": g[f"{opp_side}_team"],
            "exp_innings": round(exp_innings, 2),
            "pitch_ceiling": (workload or {}).get("pitch_ceiling"),
        }
        model = prop_model.pitcher_strikeouts(exp_innings, k_rate, opp_k,
                                              fp_k_today, ump_k_factor=ump_k)
        rows.append({**common, "market": "pitcher_strikeouts",
                     "projection": round(model["mean"], 2),
                     "dist": "poisson", "param": model["lambda"],
                     "tto_factor": model.get("tto_factor")})

        # Additional DFS-quoted pitcher markets (outs, hits/ER/walks allowed),
        # derived from expected innings + the pitcher's own per-inning rates
        # (when present on the stat row) blended with FP's daily projection.
        h9 = _lookup_float(stats_row, "H/9", "H9", "Hper9")
        er9 = _lookup_float(stats_row, "ERA")
        bb9 = _lookup_float(stats_row, "BB/9", "BB9")
        outs = prop_model.pitcher_outs(
            exp_innings, _lookup_float(fp_stats, "OUTS", "Outs"))
        hits_a = prop_model.pitcher_hits_allowed(
            exp_innings, h9 / 9 if h9 else None,
            _lookup_float(fp_stats, "H", "HA"))
        er = prop_model.pitcher_earned_runs(
            exp_innings, er9 / 9 if er9 else None,
            _lookup_float(fp_stats, "ER"))
        walks = prop_model.pitcher_walks(
            exp_innings, bb9 / 9 if bb9 else None,
            _lookup_float(fp_stats, "BB"))
        for market, m in (("pitcher_outs", outs),
                          ("pitcher_hits_allowed", hits_a),
                          ("pitcher_earned_runs", er),
                          ("pitcher_walks", walks)):
            rows.append({**common, "market": market,
                         "projection": round(m["mean"], 2), "dist": "negbinom",
                         "param": m["mean"], "dispersion": m["dispersion"]})
    return rows


def _batter_prop_rows(g: dict, batters: pd.DataFrame, fp: dict,
                      season: int) -> list[dict]:
    rows = []
    try:
        lineups = mlb_statsapi.batting_order(g["game_pk"])
    except Exception:
        return rows
    for side, opp_side in (("home", "away"), ("away", "home")):
        # opposing starter's throwing hand -> each hitter's platoon split
        vs_hand = platoon.throws(g.get(f"{opp_side}_pitcher") or "",
                                 g.get(f"{opp_side}_pitcher_id"))
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
            slg = _lookup_float(stats_row, "SLG")
            xslg = _lookup_float(stats_row, "est_slg")
            # Platoon nudge: shrink the hitter's rates toward their split vs the
            # starter's hand (shrunk by the split's PA; 1.0 when unknown).
            pid = stats_row.get("player_id")
            avg_mult = (platoon.platoon_mult(pid, vs_hand, season, ba or 0, "avg")
                        if ba and vs_hand else 1.0)
            slg_mult = (platoon.platoon_mult(pid, vs_hand, season, slg or 0, "slg")
                        if slg and vs_hand else 1.0)
            ba = ba * avg_mult if ba else ba
            slg = slg * slg_mult if slg else slg

            hits = prop_model.batter_hits(exp_ab, ba, xba, fp_h)
            tb = prop_model.batter_total_bases(exp_ab, slg, xslg, fp_tb)

            pa_total = _lookup_float(stats_row, "PA")
            hr_total = _lookup_float(stats_row, "HR")
            hr_rate = hr_total / pa_total if hr_total and pa_total else None
            if hr_rate:
                hr_rate *= slg_mult          # power scales with the platoon edge
            hr = prop_model.batter_home_run(exp_ab + 0.4, hr_rate, fp_hr)

            common = {
                "game_pk": g["game_pk"],
                "player": name,
                "team": g[f"{side}_team"],
                "opponent": g[f"{opp_side}_team"],
                "platoon": (f"vs {'LHP' if vs_hand == 'L' else 'RHP'}"
                            if vs_hand else None),
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
        disp = row.get("dispersion")
        if disp is None or pd.isna(disp):
            disp = prop_model.TB_DISPERSION
        return prop_model.prob_over_neg_binom(row["param"], line, float(disp))
    if row["dist"] == "binomial":
        return prop_model.prob_over_hits(row.get("n", 4), row["param"], line)
    if row["dist"] == "bernoulli":
        return row["param"] if line < 1 else None
    return None


_PROP_EDGE_COLS = ("line", "odds", "book_id", "model_over_prob", "ev", "kelly",
                   "bp_projection", "bp_ev", "bp_probability",
                   "bp_recommended_side", "bp_bet_rating",
                   "pp_line", "pp_over_odds", "pp_under_odds", "pp_model_over_prob",
                   "ud_line", "ud_over_odds", "ud_under_odds", "ud_model_over_prob")


# DFS operators we surface their own pick'em line for (prefix -> bettingpros id).
_DFS_BOOKS = {"pp": bettingpros.PRIZEPICKS_BOOK_ID, "ud": bettingpros.UNDERDOG_BOOK_ID}


def _attach_dfs_lines(props: pd.DataFrame, market_rows: list[dict]) -> pd.DataFrame:
    """Attach PrizePicks/Underdog pick'em lines (and our model prob *at that
    line*) from the per-book /offers rows. The DFS line frequently differs from
    the consensus, and our edge is the model probability of clearing the softer
    DFS number — not the book's de-vigged price."""
    dfs_rows = bettingpros.dfs_offer_lines(market_rows)
    if not dfs_rows:
        log.info("DFS lines: no PrizePicks/Underdog rows in %d offer rows",
                 len(market_rows))
        return props
    dfs = pd.DataFrame(dfs_rows)
    dfs["norm_player"] = dfs["participant"].map(normalize)
    if "norm_player" not in props.columns:
        props = props.assign(norm_player=props["player"].map(normalize))
    for prefix, book_id in _DFS_BOOKS.items():
        sub = dfs[dfs["book_id"] == book_id]
        log.info("DFS lines: %s (book %s) -> %d offers", prefix, book_id, len(sub))
        if sub.empty:
            continue
        sub = sub.drop_duplicates(["norm_player", "market"])
        ren = sub.rename(columns={
            "over_line": f"{prefix}_line",
            "over_odds": f"{prefix}_over_odds",
            "under_odds": f"{prefix}_under_odds",
        })[["norm_player", "market", f"{prefix}_line",
            f"{prefix}_over_odds", f"{prefix}_under_odds"]]
        props = props.merge(ren, on=["norm_player", "market"], how="left")

        def _prob(row, _p=prefix):
            line = row.get(f"{_p}_line")
            if line is None or pd.isna(line):
                return None
            p = prob_over_for_row(row, float(line))
            return round(p, 4) if p is not None else None

        props[f"{prefix}_model_over_prob"] = props.apply(_prob, axis=1)
    return props


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
    # Drive /offers off the market_ids + event_ids that actually appear on
    # today's props board — the ids resolved from /markets don't line up with
    # the offers feed, which left every prop-offers call empty (no DFS lines).
    try:
        market_rows = bettingpros.prop_offer_lines("MLB", date)
        from collections import Counter
        log.info("prop offers: %d rows across markets %s", len(market_rows),
                 dict(Counter(r.get("market") for r in market_rows)))
    except Exception as e:
        log.warning("prop offers unavailable: %s", e)
        market_rows = []

    if not market_rows:
        log.warning("no prop offers returned for any market — falling back to "
                    "BettingPros board (no per-book DFS lines available)")
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
    merged = _attach_dfs_lines(merged, market_rows)
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
                              prices.get("away"), shrink=SPORTS["MLB"].market_shrink)
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
                    # Off-grid line: price it from a negative binomial matching the
                    # simulation's run dispersion (var = mean × RUN_DISPERSION), NOT
                    # a Poisson (var = mean). A Poisson here overstates P(over) on
                    # below-mean lines and contradicts the model we actually drew.
                    p = _nb_prob_over(int(line), float(row["proj_total"]))
                out.update({"total_line": line, "over_odds": float(best_o["odds"]),
                            "model_over_prob": round(float(p), 4)})
                under_price = None
                if not unders.empty:
                    best_u = unders.sort_values("odds", ascending=False).iloc[0]
                    under_price = float(best_u["odds"])
                    out["under_odds"] = under_price
                ev = _market_eval(float(p), float(best_o["odds"]), under_price,
                                  shrink=SPORTS["MLB"].market_shrink)
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
                    shrink = SPORTS["MLB"].market_shrink
                    ev = _market_eval(float(p_cover), float(best_h["odds"]),
                                      away_price, shrink=shrink)
                    # Run-line prices are line-shopped best-of-book, so the
                    # home/away pair often sums under 1.0 (a synthetic arb across
                    # books) and the two-way de-vig rejects it. Fall back to
                    # honest per-side pricing at the real best price (same as the
                    # props board) rather than dropping the edge.
                    if ev["ev_a"] is None and away_price is not None:
                        ha = _market_eval(float(p_cover), float(best_h["odds"]),
                                          None, shrink=shrink)
                        hb = _market_eval(1.0 - float(p_cover), float(away_price),
                                          None, shrink=shrink)
                        ev = {"ev_a": ha["ev_a"], "ev_b": hb["ev_a"]}
                    if ev.get("ev_a") is not None:
                        out["rl_home_ev"] = ev["ev_a"]
                    if ev.get("ev_b") is not None:
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
    ratings = generic.team_ratings(results, sport.league_ppg, sport.opponent_adjust)

    # Elo: maintain ratings over a longer history (covers prior + current
    # season for cross-season carryover), then blend its win prob in.
    elo = None
    if sport.elo_blend > 0:
        elo = Elo(EloConfig(k=sport.elo_k, home_edge=sport.elo_home_edge,
                            season_regress=sport.elo_regress))
        try:
            elo_results = espn.results_range(
                sport_key, mlb_statsapi._shift_date(date, -500), date)
            for r in sorted(elo_results, key=lambda x: x["date"]):
                elo.update(r["home_team"], r["away_team"],
                           r["home_score"], r["away_score"], int(r["date"][:4]))
        except Exception as e:
            log.warning("%s Elo history unavailable: %s", sport_key, e)
            elo = None

    # rest days: each team's last completed game before the slate, for the
    # rest-edge adjustment (back-to-backs / byes). Built from the same results.
    last_played: dict[str, str] = {}
    for r in results:
        d = str(r["date"])[:10]
        for t in (r["home_team"], r["away_team"]):
            if d > last_played.get(t, ""):
                last_played[t] = d

    def _rest(team: str) -> int:
        from datetime import date as _D
        prev = last_played.get(team)
        if not prev:
            return 14
        return min((_D.fromisoformat(date) - _D.fromisoformat(prev)).days, 14)

    rows = []
    for g in slate:
        proj = generic.project_game(
            sport, ratings.get(g["home_team"]), ratings.get(g["away_team"])
        )
        hwp = proj.home_win_prob
        if elo is not None:
            season = int(str(g.get("date") or date)[:4])
            ewp = elo.home_win_prob(g["home_team"], g["away_team"], season)
            hwp = (1 - sport.elo_blend) * hwp + sport.elo_blend * ewp
        if sport.rest_coeff and sport.sigma_margin > 0:
            rest_diff = _rest(g["home_team"]) - _rest(g["away_team"])
            hwp = generic.shift_win_prob(hwp, sport.rest_coeff * rest_diff,
                                         sport.sigma_margin)
        # Fold the Elo/rest-adjusted win prob back into the projection's margin
        # so the spread cover prob (which reads margin_mean) stays consistent
        # with the published moneyline. Totals are unaffected by design.
        proj = generic.with_consistent_margin(proj, hwp, sport)
        hwp = proj.home_win_prob
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


def project_soccer_games(sport_key: str, date: str) -> pd.DataFrame:
    """Soccer match projections: 1X2 (home/draw/away), expected goals, over/under
    2.5, and both-teams-to-score. Expected goals come from the shared off/def team
    ratings (in goals) + home field; the scoreline distribution and draw come from
    models/soccer (Dixon-Coles-corrected Poisson). Projection-only for now — no
    odds feed is wired for soccer, so there is no EV/edge column yet; the numbers
    surface and, once priced, flow through the edge gate in PROBATION like any new
    market."""
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
    ratings = generic.team_ratings(results, sport.league_ppg, sport.opponent_adjust)
    rows = []
    for g in slate:
        h_exp, a_exp = generic.expected_score(
            sport, ratings.get(g["home_team"]), ratings.get(g["away_team"]))
        probs = soccer.outcome_probs(h_exp, a_exp)
        rows.append({
            "game_id": g["game_id"], "game_time": g["game_time"],
            "away_team": g["away_team"], "home_team": g["home_team"],
            "home_exp": round(h_exp, 2), "away_exp": round(a_exp, 2),
            "proj_total": round(h_exp + a_exp, 2),
            "home_win_prob": probs["home"], "draw_prob": probs["draw"],
            "away_win_prob": probs["away"],
            "over_2_5": soccer.over_prob(h_exp, a_exp, 2.5),
            "btts": soccer.btts_prob(h_exp, a_exp),
        })
    return pd.DataFrame(rows)


def project_tennis_matches(sport_key: str, date: str) -> pd.DataFrame:
    """Tennis match projections: P(each player wins) from surface-aware player
    Elo (models/tennis), primed from ESPN match history. Projection-only for now
    (no odds feed), surfaced and, once priced, gated in PROBATION like any new
    market."""
    slate = espn.tennis_matches(sport_key, date, date)
    slate = [m for m in slate if not m["completed"]]
    if not slate:
        return pd.DataFrame()
    sport = SPORTS[sport_key]
    start = mlb_statsapi._shift_date(date, -sport.form_days)
    elo = tennis.TennisElo()
    try:
        history = espn.tennis_matches(sport_key, start, date, completed_only=True)
        for m in sorted(history, key=lambda x: x["date"]):
            elo.update(m["winner"],
                       m["player2"] if m["winner"] == m["player1"] else m["player1"],
                       m.get("surface"))
    except Exception as e:
        log.warning("%s Elo history unavailable: %s", sport_key, e)
    rows = []
    for m in slate:
        p1, p2, surf = m["player1"], m["player2"], m.get("surface")
        p1_win = elo.match_prob(p1, p2, surf)
        rows.append({
            "match_id": m["match_id"], "match_time": m["match_time"],
            "tournament": m["tournament"], "surface": surf,
            "player1": p1, "player2": p2,
            "player1_win_prob": p1_win, "player2_win_prob": round(1 - p1_win, 4),
            "p1_matches": elo.seen(p1), "p2_matches": elo.seen(p2),
        })
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
            ev = _market_eval(r["home_win_prob"], r.get("home_ml"), r.get("away_ml"),
                              shrink=sport.market_shrink)
            return pd.Series({"home_ml_ev": ev["ev_a"], "away_ml_ev": ev["ev_b"]})

        games = pd.concat([games, games.apply(_ml_ev, axis=1)], axis=1)

    tot = flat_by_market.get("total", pd.DataFrame())
    if not tot.empty and "line" in tot.columns:
        event_teams = _bp_event_teams(events)
        sel = tot["selection"].astype(str).str.lower()
        overs = tot[sel.str.contains("over", na=False) & tot["line"].notna()]
        overs = overs.sort_values("odds", ascending=False).drop_duplicates("event_id")
        by_event = {r["event_id"]: r for _, r in overs.iterrows()}
        unders = tot[sel.str.contains("under", na=False) & tot["line"].notna()]
        unders = unders.sort_values("odds", ascending=False).drop_duplicates("event_id")
        under_by_event = {r["event_id"]: r for _, r in unders.iterrows()}

        def total_cols(row):
            offer = under = None
            for eid, teams in event_teams.items():
                if eid in by_event and any(
                    _teams_match(row["home_team"], t) or _teams_match(row["away_team"], t)
                    for t in teams
                ):
                    offer = by_event[eid]
                    under = under_by_event.get(eid)
                    break
            if offer is None:
                return pd.Series({"total_line": None, "over_odds": None,
                                  "under_odds": None, "model_over_prob": None,
                                  "over_ev": None, "under_ev": None})
            line = float(offer["line"])
            p = row["_proj"].prob_over(line, sport)
            under_odds = float(under["odds"]) if under is not None else None
            ev = _market_eval(p, float(offer["odds"]), under_odds,
                              shrink=SPORTS[sport].market_shrink)
            return pd.Series({
                "total_line": line,
                "over_odds": offer["odds"],
                "under_odds": under_odds,
                "model_over_prob": round(p, 4),
                "over_ev": ev["ev_a"],
                "under_ev": ev["ev_b"],
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
            ev = _market_eval(p, float(best_h["odds"]), away_price,
                              shrink=sport.market_shrink)
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
                         "total_line", "over_odds", "under_odds", "model_over_prob",
                         "over_ev", "under_ev", "spread_home_line", "spread_home_odds",
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
    # Basketball
    "point": ("PTS", "points"),
    "rebound": ("REB", "rebounds"),
    "assist": ("AST", "assists"),
    "three": ("3PM", "THREES", "three_pointers"),
    "steal": ("STL", "steals"),
    "block": ("BLK", "blocks"),
    # Football (multiple candidate spellings; falls back to BettingPros'
    # projection when FantasyPros' exact key differs — verify live in-season).
    "passing yard": ("PASS_YDS", "passing_yards", "pass_yds", "PYDS"),
    "rushing yard": ("RUSH_YDS", "rushing_yards", "rush_yds", "RYDS"),
    "receiving yard": ("REC_YDS", "receiving_yards", "rec_yds", "RECYDS"),
    "reception": ("REC", "receptions"),
    "passing touchdown": ("PASS_TDS", "passing_tds", "pass_td", "PTD"),
    "rushing touchdown": ("RUSH_TDS", "rushing_tds", "rush_td"),
    "receiving touchdown": ("REC_TDS", "receiving_tds", "rec_td"),
    "interception": ("INTS", "interceptions", "INT"),
    "completion": ("CMP", "completions"),
    "field goal": ("FG", "field_goals", "fg_made"),
}


def _nfl_week(date: str) -> int:
    """Approximate NFL week number for a date. Week 1 ≈ the first Thursday on
    or after Sept 1 (real kickoff is the Thursday after Labor Day — close
    enough to fetch weekly projections; refine against the schedule live)."""
    from datetime import date as _D, timedelta
    d = _D.fromisoformat(date)
    season = d.year if d.month >= 8 else d.year - 1
    sept1 = _D(season, 9, 1)
    kickoff = sept1 + timedelta(days=(3 - sept1.weekday()) % 7)  # Thursday = 3
    return max(1, min((d - kickoff).days // 7 + 1, 18))


def _fp_generic_index(sport_key: str, date: str) -> dict[str, dict]:
    """FantasyPros projections for sports that have them (NBA daily, NFL
    weekly). Empty dict when unsupported or unavailable."""
    sp = SPORTS[sport_key]
    if sport_key == "MLB" or not sp.fp_projections:
        return {}
    try:
        if sport_key == "NBA":
            players = fantasypros.nba_projections(_season(date), date)
        elif sport_key == "NFL":
            from datetime import date as _D
            d = _D.fromisoformat(date)
            season = d.year if d.month >= 8 else d.year - 1
            players = fantasypros.nfl_projections(season, _nfl_week(date))
        else:
            return {}
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


# sports that price props from our own committed box-score logs (validated,
# well-calibrated per-player NB models) rather than only the vendor projection.
_LOG_PROP_MODELS = {"WNBA": wnba_props, "NHL": nhl_props, "NBA": nba_props}


# sports whose log prop model exposes an opponent adjustment, with the stat
# columns their defense table needs from the committed logs.
_OPP_ADJUST_COLS = {
    "WNBA": ("points", "rebounds", "assists", "three_made", "pra"),
    "NBA": ("points", "rebounds", "assists", "three_made", "pra"),
    "NHL": ("shots", "points", "goals", "assists", "blocks", "saves"),
}


@lru_cache(maxsize=16)
def _defense_table(sport_key: str, season: int) -> tuple:
    """Opponent-defense multipliers per (team, market) from a sport's committed
    box-score logs, keyed by canonical team so it joins the slate. Cached per
    (sport, season); returned as a tuple of items so lru_cache can hold it."""
    model = _LOG_PROP_MODELS.get(sport_key)
    cols = _OPP_ADJUST_COLS.get(sport_key)
    if model is None or cols is None or not hasattr(model, "defense_factors"):
        return ()
    # Season labelling differs by sport (WNBA = calendar year, NHL = the season's
    # start year), and NHL/NBA are queried out of season — so build from the most
    # recent season that actually has logs within a short lookback window.
    try:
        df = playerlogs._logs(sport_key, tuple(range(season - 2, season + 1)))
    except Exception:
        return ()
    if df.empty or "season" not in df.columns:
        return ()
    latest = df["season"].dropna().max()
    df = df[df["season"] == latest]
    rows = []
    for rec in df.to_dict("records"):
        opp = rec.get("opp")
        if not opp:
            continue
        row = {"team": teams.canon(sport_key, str(opp))}
        for c in cols:
            if c in rec:
                row[c] = rec[c]
        rows.append(row)
    return tuple(model.defense_factors(rows).items())


def _slate_opponent_map(sport_key: str, date: str) -> dict:
    """{canonical_team: canonical_opponent} for a date, from the ESPN slate."""
    out: dict = {}
    try:
        for g in espn.slate(sport_key, date):
            h = teams.canon(sport_key, g.get("home_team") or "")
            a = teams.canon(sport_key, g.get("away_team") or "")
            if h and a:
                out[h], out[a] = a, h
    except Exception:
        pass
    return out


def _log_model_prop(sport_key: str, player: str, market_name: str, date: str,
                    opponent: str | None = None, def_table: dict | None = None):
    """Our own per-player projection + NB dispersion for a prop market, from the
    committed box-score logs (wnba_props / nhl_props). Returns (proj, r), or
    (None, None) when the sport/market isn't covered or the player's sample is
    too thin — in which case the vendor projection and the generic distribution
    are used unchanged. Both models are held-out validated (ECE ~0.01); they
    ship behind the demonstrated-edge gate like every market. When the model
    exposes an opponent adjustment (WNBA) and the matchup is known, the
    projection is nudged by the opposing team's defense in that market."""
    model = _LOG_PROP_MODELS.get(sport_key)
    if model is None:
        return None, None
    key = model.canonical_market(market_name)
    if key is None:
        return None, None
    try:
        season = int(str(date)[:4])
        series = [pt["value"] for pt in playerlogs.recent_series(
            sport_key, player, key, n=40, season=season)]
    except Exception:
        return None, None
    if len(series) < model.MIN_GAMES:
        return None, None
    proj = model.project(series, key)
    if not proj:
        return None, None
    mean = proj.proj
    if opponent and def_table and hasattr(model, "opponent_factor"):
        mean = mean * model.opponent_factor(market_name, opponent, def_table)
    return round(mean, 2), proj.r


def project_generic_props(sport_key: str, date: str) -> pd.DataFrame:
    """Props for non-MLB sports: BettingPros lines + premium projections,
    blended with FantasyPros where available, with our distribution on top.

    For WNBA and NHL we add our own per-player projection (from the committed
    box-score logs) as a third source and price it with our data-fit, calibrated
    negative binomial instead of the generic keyword dispersion."""
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

    # Opponent adjustment (WNBA, NHL): the day's matchups + a defense-vs-market
    # table from the committed logs, so each player's projection is nudged by who
    # they face. Both keyed by canonical team so they join cleanly. Empty (no-op)
    # for sports without an opponent model or out of season (empty slate).
    opp_adjust = sport_key in _OPP_ADJUST_COLS
    opp_map = _slate_opponent_map(sport_key, date) if opp_adjust else {}
    def_table = dict(_defense_table(sport_key, int(str(date)[:4]))) if opp_adjust else {}

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
        opponent = opp_map.get(teams.canon(sport_key, r.get("player_team") or "")) \
            if opp_map else None
        model_proj, model_r = _log_model_prop(
            sport_key, r["participant"], market_name, date,
            opponent=opponent, def_table=def_table)
        sources = [float(v) for v in (fp_proj, bp_proj, model_proj)
                   if isinstance(v, (int, float)) and pd.notna(v)]
        projection = sum(sources) / len(sources) if sources else None

        line = r.get("over_line") if pd.notna(r.get("over_line")) else r.get("bp_line")
        row = {
            "market": market_name,
            "player": r["participant"],
            "team": r.get("player_team"),
            "opponent": opponent,
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
            "model_projection": round(model_proj, 2) if model_proj is not None else None,
            "model_over_prob": None, "ev_over": None, "ev_under": None, "kelly": None,
        }
        if projection is not None and pd.notna(line):
            if model_r is not None:      # WNBA: our data-fit, calibrated NB
                p_over = wnba_props.prob_over(float(projection), float(line), model_r)
            else:                        # other sports: generic keyword dispersion
                p_over = generic.prop_prob_over(float(projection), float(line), market_name)
            row["model_over_prob"] = round(p_over, 4)
            ev = _market_eval(p_over, r.get("over_odds"), r.get("under_odds"),
                              shrink=SPORTS[sport_key].market_shrink)
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


def _records(df) -> list[dict]:
    return df.to_dict(orient="records") if hasattr(df, "to_dict") else (df or [])


def _safe_step(fn, label: str, key: str) -> tuple[list[dict], str | None]:
    """Run one pipeline step, returning (records, error_note). A failure in one
    half (e.g. props hitting a data KeyError) no longer blanks the other half
    (games) — the slate stays alive with whatever succeeded."""
    try:
        return _records(fn()), None
    except Exception as e:
        log.error("%s %s step failed: %s", key, label, e)
        return [], f"{label} unavailable ({type(e).__name__}: {e})"


def _bundle(games: list[dict], props: list[dict], *errs: str | None) -> dict:
    out = {"games": games, "props": props}
    note = "; ".join(e for e in errs if e)
    if note:
        out["error"] = note
    return out


def _skip_props(games, pull_props, sport_key, date, reason) -> bool:
    """Whether to skip the (BettingPros-spending) prop pull this run."""
    if not games:               # no games on the slate -> nothing to price
        log.info("%s: no games on %s — skipping prop pulls", sport_key, date)
        return True
    if not pull_props:          # outside the prop-pull window (budget guard)
        log.info("%s: %s — skipping prop pulls (games still built)",
                 sport_key, reason)
        return True
    return False


def _run_mlb(date: str, pull_props: bool = True) -> dict:
    games, ge = _safe_step(
        lambda: attach_game_edges(project_games(date), date), "games", "MLB")
    if _skip_props(games, pull_props, "MLB", date, "prop window closed"):
        return _bundle(games, [], ge, None)
    props, pe = _safe_step(
        lambda: attach_hit_rates(attach_prop_edges(project_props(date), date),
                                 "MLB", date), "props", "MLB")
    return _bundle(games, props, ge, pe)


def _run_generic(sport_key: str, date: str, pull_props: bool = True) -> dict:
    games, ge = _safe_step(
        lambda: attach_generic_game_edges(
            project_generic_games(sport_key, date), sport_key, date),
        "games", sport_key)
    if _skip_props(games, pull_props, sport_key, date, "prop window closed"):
        return _bundle(games, [], ge, None)
    props, pe = _safe_step(
        lambda: attach_hit_rates(project_generic_props(sport_key, date),
                                 sport_key, date), "props", sport_key)
    return _bundle(games, props, ge, pe)


# Match-model sports with no shared prop board: projection-only (1X2/totals for
# soccer, match win prob for tennis), surfaced and gated in PROBATION once priced.
_SOCCER_SPORTS = {"MLS", "EPL"}
_TENNIS_SPORTS = {"ATP", "WTA"}


def _run_soccer(sport_key: str, date: str) -> dict:
    games, ge = _safe_step(
        lambda: project_soccer_games(sport_key, date), "games", sport_key)
    return _bundle(games, [], ge, None)


def _run_tennis(sport_key: str, date: str) -> dict:
    games, ge = _safe_step(
        lambda: project_tennis_matches(sport_key, date), "games", sport_key)
    return _bundle(games, [], ge, None)


def _dispatch(key: str, date: str, pull_props: bool) -> dict:
    if key == "MLB":
        return _run_mlb(date, pull_props)
    if key in _SOCCER_SPORTS:
        return _run_soccer(key, date)
    if key in _TENNIS_SPORTS:
        return _run_tennis(key, date)
    return _run_generic(key, date, pull_props)


def run(date: str | None = None, sports: list[str] | None = None,
        write: bool = True, pull_props: bool = True) -> dict:
    date = date or _date.today().isoformat()
    sports = sports or active_sports(date)

    out_sports = {}
    for key in sports:
        if key not in SPORTS:
            log.warning("unknown sport %s, skipping", key)
            continue
        try:
            out_sports[key] = _dispatch(key, date, pull_props)
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
