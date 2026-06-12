"""Walk-forward backtesting for the game models, graded against actual
results and (where available) closing lines.

No lookahead: every game's team ratings are built only from games that
finished before it. Betting metrics are measured at *closing* prices,
which is the conservative bar — a positive ROI there means the model
beat the closing line, not just an early soft number.

Limitations worth knowing when reading the output:
  - MLB runs the production Monte-Carlo game model but with no probable
    starter (opp_starter_xfip=None), because historical probable-starter
    data wasn't imported. This measures the team-form core only; the live
    model adds starter xFIP, which is most of its edge. Treat MLB game
    numbers as a floor.
  - WNBA runs the exact production generic model (offense/defense ratings
    + home edge), so those numbers reflect the real system.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import config, history, odds, parks
from .models import game as mlb_game
from .models import generic
from .models import props as mlb_props
from .names import normalize
from .sports import SPORTS

# WNBA abbreviation -> team nickname (last token of the full name) so the
# abbreviated backfill joins to the full-name closing lines.
WNBA_ABBR_TO_NICK = {
    "ATL": "dream", "CHI": "sky", "CON": "sun", "CONN": "sun", "DAL": "wings",
    "GS": "valkyries", "GSV": "valkyries", "IND": "fever", "LA": "sparks",
    "LAS": "sparks", "LV": "aces", "LVA": "aces", "MIN": "lynx", "NY": "liberty",
    "NYL": "liberty", "PHX": "mercury", "PHO": "mercury", "POR": "fire",
    "SEA": "storm", "WSH": "mystics", "WAS": "mystics", "TOR": "tempo",
}


def _nick_from_full(name: str) -> str:
    return str(name).strip().lower().split()[-1] if name else ""


def _team_key(sport_key: str, home: str, away: str) -> tuple:
    """Team part of the join key. MLB matches on normalized full names
    (both sources use them); WNBA bridges abbreviations <-> full names via
    team nicknames."""
    if sport_key == "WNBA":
        return (WNBA_ABBR_TO_NICK.get(home, _nick_from_full(home)),
                WNBA_ABBR_TO_NICK.get(away, _nick_from_full(away)))
    return (normalize(home), normalize(away))


def _lookup_closing(consensus: dict, sport_key: str, date: str,
                    home: str, away: str) -> dict | None:
    """Find the closing record for a game, tolerating a ±1 day offset
    (closing_start is UTC, so late games roll a day past the local game
    date). Picks the closest date when several qualify."""
    import datetime as _dt

    bucket = consensus.get(_team_key(sport_key, home, away))
    if not bucket:
        return None
    d0 = _dt.date.fromisoformat(date)
    best, best_gap = None, 2
    for cand_date, rec in bucket:
        gap = abs((_dt.date.fromisoformat(cand_date) - d0).days)
        if gap <= 1 and gap < best_gap:
            best, best_gap = rec, gap
    return best


# ---------------------------------------------------------------------------
# Game loading -> a uniform list of dicts sorted by date
# ---------------------------------------------------------------------------

def _mlb_games(seasons: list[int], use_results_2026: bool) -> list[dict]:
    """date, home, away, home_score, away_score, game_pk. Uses full-name
    results for 2026 (joins to closing lines) and abbreviated backfill
    otherwise. game_pk lets us attach starters from player_games."""
    rows = []
    for s in seasons:
        if s == 2026 and use_results_2026:
            df = history.results("mlb", 2026)
            for _, r in df.iterrows():
                if r.get("status") not in (None, "final", "Final"):
                    continue
                pk = str(r.get("game_id", "")).split("-")[-1]
                rows.append({
                    "date": str(r["date"])[:10], "home": r["home_team"],
                    "away": r["away_team"], "home_score": r["home_score"],
                    "away_score": r["away_score"],
                    "game_pk": int(pk) if pk.isdigit() else None,
                })
        else:
            df = history.backfill_games("mlb", seasons=[s])
            for _, r in df.iterrows():
                rows.append({
                    "date": str(r["date"])[:10], "home": r["home_team"],
                    "away": r["away_team"], "home_score": r["home_score"],
                    "away_score": r["away_score"],
                    "game_pk": int(r["game_pk"]) if pd.notna(r.get("game_pk")) else None,
                })
    rows = [r for r in rows if pd.notna(r["home_score"]) and pd.notna(r["away_score"])]
    rows.sort(key=lambda r: r["date"])
    return rows


def starter_fip_table(seasons: list[int], league_fip: float = 4.10,
                      ip_prior: float = 50.0, min_ip: float = 5.0) -> dict:
    """As-of-date starter FIP per game side, no lookahead. Walks the
    started-pitcher box logs (player_games, MLB 2024+) in date order and
    records each starter's FIP from *prior* starts only, shrunk toward
    league with an innings prior. Returns {(game_pk, 'home'|'away'): fip}.

        FIP = (13*HR + 3*(BB+HBP) - 2*K) / IP + 3.10
        shrunk = (FIP*IP + league*IP_PRIOR) / (IP + IP_PRIOR)
    """
    pg = history.player_games("mlb", seasons=seasons)
    if pg.empty or "started" not in pg.columns:
        return {}
    sp = pg[(pg["position"] == "P") & (pg["started"] == True)].copy()  # noqa: E712
    sp["dt"] = pd.to_datetime(sp["date"])
    sp.sort_values("dt", inplace=True)

    cum: dict = defaultdict(lambda: {"hr": 0.0, "bb": 0.0, "hbp": 0.0, "k": 0.0, "ip": 0.0})
    out: dict = {}
    for _, row in sp.iterrows():
        pid = row["player_id"]
        c = cum[pid]
        if c["ip"] >= min_ip:
            raw = (13 * c["hr"] + 3 * (c["bb"] + c["hbp"]) - 2 * c["k"]) / c["ip"] + 3.10
            fip = (raw * c["ip"] + league_fip * ip_prior) / (c["ip"] + ip_prior)
        else:
            fip = None
        pk = row.get("game_pk")
        if pd.notna(pk):
            side = "home" if row.get("is_home") else "away"
            out[(int(pk), side)] = fip
        st = row.get("stats") or {}
        c["hr"] += st.get("homeRuns", 0) or 0
        c["bb"] += st.get("baseOnBalls", 0) or 0
        c["hbp"] += st.get("hitByPitch", 0) or 0
        c["k"] += st.get("strikeOuts", 0) or 0
        c["ip"] += float(st.get("inningsPitched", 0) or 0)
    return out


def bullpen_fip_table(seasons: list[int], league_fip: float = 4.10,
                      ip_prior: float = 120.0, min_ip: float = 20.0) -> dict:
    """As-of-date team bullpen FIP per game side, no lookahead. Aggregates
    relief appearances (started=False) per team cumulatively in date order;
    records each team's bullpen FIP from *prior* games before each game.
    Returns {(game_pk, 'home'|'away'): bullpen_fip}. Larger IP prior than
    starters since bullpens pool many arms."""
    pg = history.player_games("mlb", seasons=seasons)
    if pg.empty or "started" not in pg.columns:
        return {}
    rp = pg[(pg["position"] == "P") & (pg["started"] == False)].copy()  # noqa: E712
    rp["dt"] = pd.to_datetime(rp["date"])
    # one aggregated reliever line per (date, game_pk, team, side)
    def _agg(s):
        tot = defaultdict(float)
        for st in s:
            st = st or {}
            tot["hr"] += st.get("homeRuns", 0) or 0
            tot["bb"] += st.get("baseOnBalls", 0) or 0
            tot["hbp"] += st.get("hitByPitch", 0) or 0
            tot["k"] += st.get("strikeOuts", 0) or 0
            tot["ip"] += float(st.get("inningsPitched", 0) or 0)
        return tot

    rp["side"] = rp["is_home"].map(lambda x: "home" if x else "away")
    grouped = (rp.sort_values("dt")
                 .groupby(["dt", "game_pk", "team", "side"])["stats"]
                 .apply(list).reset_index())
    grouped["agg"] = grouped["stats"].map(_agg)
    grouped.sort_values("dt", inplace=True)

    cum: dict = defaultdict(lambda: {"hr": 0.0, "bb": 0.0, "hbp": 0.0, "k": 0.0, "ip": 0.0})
    out: dict = {}
    for _, row in grouped.iterrows():
        team = row["team"]
        c = cum[team]
        if c["ip"] >= min_ip:
            raw = (13 * c["hr"] + 3 * (c["bb"] + c["hbp"]) - 2 * c["k"]) / c["ip"] + 3.10
            fip = (raw * c["ip"] + league_fip * ip_prior) / (c["ip"] + ip_prior)
        else:
            fip = None
        if pd.notna(row["game_pk"]):
            out[(int(row["game_pk"]), row["side"])] = fip
        a = row["agg"]
        for k in ("hr", "bb", "hbp", "k", "ip"):
            c[k] += a[k]
    return out


def _wnba_games(seasons: list[int]) -> list[dict]:
    df = history.backfill_games("wnba", seasons=seasons)
    rows = []
    for _, r in df.iterrows():
        if not r.get("completed", True):
            continue
        rows.append({
            "date": str(r["date"])[:10], "home": r["home_team"], "away": r["away_team"],
            "home_score": r["home_score"], "away_score": r["away_score"],
            "home_nick": WNBA_ABBR_TO_NICK.get(r["home_team"]),
            "away_nick": WNBA_ABBR_TO_NICK.get(r["away_team"]),
        })
    rows = [r for r in rows if pd.notna(r["home_score"]) and pd.notna(r["away_score"])]
    rows.sort(key=lambda r: r["date"])
    return rows


# ---------------------------------------------------------------------------
# Walk-forward ratings
# ---------------------------------------------------------------------------

@dataclass
class _Form:
    window: int
    scored: dict = field(default_factory=lambda: defaultdict(deque))
    allowed: dict = field(default_factory=lambda: defaultdict(deque))

    def rating(self, team: str, league_ppg: float) -> generic.TeamRating | None:
        s, a = self.scored[team], self.allowed[team]
        n = len(s)
        if n == 0:
            return None
        w = generic.RATING_SHRINK * min(1.0, n / 10)
        return generic.TeamRating(
            games=n,
            scored=w * (sum(s) / n) + (1 - w) * league_ppg,
            allowed=w * (sum(a) / n) + (1 - w) * league_ppg,
        )

    def update(self, g: dict):
        for team, sc, al in ((g["home"], g["home_score"], g["away_score"]),
                             (g["away"], g["away_score"], g["home_score"])):
            self.scored[team].append(sc)
            self.allowed[team].append(al)
            if len(self.scored[team]) > self.window:
                self.scored[team].popleft()
                self.allowed[team].popleft()


def _project(sport_key: str, sport, h: generic.TeamRating | None,
             a: generic.TeamRating | None, draws: int,
             home_opp_xfip: float | None = None,
             away_opp_xfip: float | None = None,
             home_opp_bp: float | None = None,
             away_opp_bp: float | None = None,
             park_venue: float = 1.0,
             home_own_pf: float = 1.0, away_own_pf: float = 1.0):
    """Return (home_win_prob, total_mean, prob_over_fn, home_cover_fn).

    home_opp_xfip / away_opp_xfip are the FIP of the starter each team
    *faces* (home_opp_xfip = the away team's starter); home_opp_bp /
    away_opp_bp likewise for bullpens. park_venue is the home park's run
    factor; *_own_pf are each team's home park (to de-bias their rate).
    All default to neutral, giving the team-form-only model.
    """
    if sport_key == "MLB":
        hi = mlb_game.TeamInputs(name="h", runs_per_game=h.scored,
                                 opp_starter_xfip=home_opp_xfip,
                                 opp_bullpen_xfip=home_opp_bp,
                                 park_factor=park_venue, own_home_pf=home_own_pf)
        ai = mlb_game.TeamInputs(name="a", runs_per_game=a.scored,
                                 opp_starter_xfip=away_opp_xfip,
                                 opp_bullpen_xfip=away_opp_bp,
                                 park_factor=park_venue, own_home_pf=away_own_pf)
        proj = mlb_game.simulate(hi, ai, total_lines=[], runline_spreads=[],
                                 draws=draws, seed=7)
        lam_h, lam_a = proj.home_exp_runs, proj.away_exp_runs
        return (proj.home_win_prob, proj.total_mean,
                lambda line: float(1 - _poisson_cdf(int(line), lam_h + lam_a)),
                lambda spread: _poisson_cover(lam_h, lam_a, spread, draws))
    gp = generic.project_game(sport, h, a)
    return (gp.home_win_prob, gp.total_mean,
            lambda line: gp.prob_over(line, sport),
            lambda spread: gp.home_cover_prob(spread, sport))


def _poisson_cdf(k: int, lam: float) -> float:
    from scipy import stats
    return float(stats.poisson.cdf(k, lam))


def _poisson_cover(lam_h, lam_a, spread, draws):
    rng = np.random.default_rng(11)
    h = rng.poisson(lam_h, draws); a = rng.poisson(lam_a, draws)
    return float((h - a + spread > 0).mean())


# ---------------------------------------------------------------------------
# Closing-line consensus
# ---------------------------------------------------------------------------

def closing_consensus(sport: str) -> dict[tuple, dict]:
    """Keyed by the sport's _join_key -> consensus closing market data:
    de-vigged fair probs + best available prices per side."""
    cl = history.closing_lines(sport)
    if cl.empty:
        return {}
    cl = cl.copy()
    cl["date"] = cl["scheduled_start"].str[:10]  # UTC date; ±1d in lookup
    # latest capture per book/market/side
    cl = cl.sort_values("captured_at").groupby(
        ["event_id", "book", "market", "side"], as_index=False).last()

    out: dict[tuple, list] = defaultdict(list)
    for event_id, ev in cl.groupby("event_id"):
        first = ev.iloc[0]
        key = _team_key(sport, first["home_team"], first["away_team"])
        rec = {"date": first["date"], "home": first["home_team"],
               "away": first["away_team"], "moneyline": None, "total": None}

        ml = ev[ev["market"] == "moneyline"]
        home_fairs, away_fairs = [], []
        for _, bk in ml.groupby("book"):
            h = bk[bk["side"] == "home"]; a = bk[bk["side"] == "away"]
            if len(h) and len(a):
                ph = odds.implied_prob(h.iloc[0]["american_odds"])
                pa = odds.implied_prob(a.iloc[0]["american_odds"])
                fh, _ = odds.devig_two_way(ph, pa)
                home_fairs.append(fh); away_fairs.append(1 - fh)
        if home_fairs:
            rec["moneyline"] = {
                "home_fair": float(np.mean(home_fairs)),
                "away_fair": float(np.mean(away_fairs)),
                "home_best": float(ml[ml["side"] == "home"]["american_odds"].max()),
                "away_best": float(ml[ml["side"] == "away"]["american_odds"].max()),
            }

        tot = ev[ev["market"] == "total"]
        over_fairs, lines = [], []
        for _, bk in tot.groupby("book"):
            o = bk[bk["side"] == "over"]; u = bk[bk["side"] == "under"]
            if len(o) and len(u):
                po = odds.implied_prob(o.iloc[0]["american_odds"])
                pu = odds.implied_prob(u.iloc[0]["american_odds"])
                fo, _ = odds.devig_two_way(po, pu)
                over_fairs.append(fo)
                lines.append(o.iloc[0]["line"])
        if over_fairs:
            rec["total"] = {
                "line": float(np.median([l for l in lines if pd.notna(l)])),
                "over_fair": float(np.mean(over_fairs)),
                "over_best": float(tot[tot["side"] == "over"]["american_odds"].max()),
                "under_best": float(tot[tot["side"] == "under"]["american_odds"].max()),
            }
        out[key].append((rec["date"], rec))
    return dict(out)


# ---------------------------------------------------------------------------
# The backtest
# ---------------------------------------------------------------------------

@dataclass
class BetLog:
    n: int = 0
    wins: int = 0
    staked: float = 0.0
    pnl: float = 0.0

    def add(self, won: bool, dec_odds: float):
        self.n += 1
        self.staked += 1.0
        if won:
            self.wins += 1
            self.pnl += dec_odds - 1.0
        else:
            self.pnl -= 1.0

    def summary(self) -> dict:
        return {"bets": self.n, "win_rate": round(self.wins / self.n, 4) if self.n else None,
                "units": round(self.pnl, 2),
                "roi_pct": round(100 * self.pnl / self.staked, 2) if self.staked else None}


def run_game_backtest(sport_key: str, seasons: list[int], min_games: int = 10,
                      draws: int = 4000, min_edge: float | None = None,
                      use_starters: bool = False, use_bullpen: bool = False,
                      use_park: bool = False) -> dict:
    min_edge = config.MIN_EDGE if min_edge is None else min_edge
    sport = SPORTS[sport_key]
    games = (_mlb_games(seasons, use_results_2026=True) if sport_key == "MLB"
             else _wnba_games(seasons))
    window = 30 if sport_key == "MLB" else 15
    form = _Form(window)
    consensus = closing_consensus(sport_key)
    is_mlb = sport_key == "MLB"
    fip_table = starter_fip_table(seasons) if (is_mlb and use_starters) else {}
    bp_table = bullpen_fip_table(seasons) if (is_mlb and use_bullpen) else {}

    # accuracy accumulators
    brier_sum = logloss_sum = n_ml = 0.0
    fav_correct = 0
    total_abs = total_sq = n_tot = 0.0
    cal_bins = defaultdict(lambda: [0, 0.0])  # bin -> [count, wins]
    # betting accumulators
    ml_bets, total_bets = BetLog(), BetLog()
    clv_deltas = []
    n_matched = 0
    n_with_starter = 0

    for g in games:
        h = form.rating(g["home"], sport.league_ppg)
        a = form.rating(g["away"], sport.league_ppg)
        if h and a and h.games >= min_games and a.games >= min_games:
            pk = g.get("game_pk")
            # home faces the away starter/bullpen and vice-versa
            home_opp = fip_table.get((pk, "away")) if fip_table else None
            away_opp = fip_table.get((pk, "home")) if fip_table else None
            home_bp = bp_table.get((pk, "away")) if bp_table else None
            away_bp = bp_table.get((pk, "home")) if bp_table else None
            if home_opp is not None or away_opp is not None:
                n_with_starter += 1
            if is_mlb and use_park:
                pf_venue = parks.factor(g["home"])
                home_pf, away_pf = pf_venue, parks.factor(g["away"])
            else:
                pf_venue = home_pf = away_pf = 1.0
            hwp, tmean, prob_over, _ = _project(
                sport_key, sport, h, a, draws, home_opp, away_opp,
                home_bp, away_bp, pf_venue, home_pf, away_pf)
            home_won = 1 if g["home_score"] > g["away_score"] else 0
            actual_total = g["home_score"] + g["away_score"]

            # ML accuracy
            p = min(max(hwp, 1e-6), 1 - 1e-6)
            brier_sum += (p - home_won) ** 2
            logloss_sum += -(home_won * math.log(p) + (1 - home_won) * math.log(1 - p))
            fav_correct += int((p >= 0.5) == bool(home_won))
            cal = cal_bins[round(p * 10) / 10]
            cal[0] += 1; cal[1] += home_won
            n_ml += 1
            # total accuracy
            total_abs += abs(tmean - actual_total)
            total_sq += (tmean - actual_total) ** 2
            n_tot += 1

            # betting vs closing
            rec = _lookup_closing(consensus, sport_key, g["date"], g["home"], g["away"])
            if rec:
                n_matched += 1
                if rec["moneyline"]:
                    m = rec["moneyline"]
                    clv_deltas.append(hwp - m["home_fair"])
                    for side, prob, price, won in (
                        ("home", hwp, m["home_best"], home_won == 1),
                        ("away", 1 - hwp, m["away_best"], home_won == 0)):
                        if odds.expected_value(prob, price) >= min_edge:
                            ml_bets.add(won, odds.american_to_decimal(price))
                if rec["total"]:
                    t = rec["total"]
                    po = prob_over(t["line"])
                    over_won = actual_total > t["line"]
                    push = actual_total == t["line"]
                    if not push:
                        if odds.expected_value(po, t["over_best"]) >= min_edge:
                            total_bets.add(over_won, odds.american_to_decimal(t["over_best"]))
                        if odds.expected_value(1 - po, t["under_best"]) >= min_edge:
                            total_bets.add(not over_won, odds.american_to_decimal(t["under_best"]))
        form.update(g)

    calibration = {b: {"n": c[0], "predicted": round(b, 2),
                       "empirical": round(c[1] / c[0], 4)}
                   for b, c in sorted(cal_bins.items()) if c[0] >= 20}
    return {
        "sport": sport_key, "seasons": seasons, "n_games_graded": int(n_ml),
        "use_starters": use_starters, "games_with_starter": n_with_starter,
        "moneyline": {
            "brier": round(brier_sum / n_ml, 4) if n_ml else None,
            "log_loss": round(logloss_sum / n_ml, 4) if n_ml else None,
            "favorite_hit_rate": round(fav_correct / n_ml, 4) if n_ml else None,
        },
        "total": {
            "mae": round(total_abs / n_tot, 3) if n_tot else None,
            "rmse": round(math.sqrt(total_sq / n_tot), 3) if n_tot else None,
        },
        "calibration": list(calibration.values()),
        "closing_line": {
            "games_matched": n_matched,
            "avg_clv_vs_fair": round(float(np.mean(clv_deltas)), 4) if clv_deltas else None,
            "moneyline_bets": ml_bets.summary(),
            "total_bets": total_bets.summary(),
        },
    }


# ---------------------------------------------------------------------------
# True CLV: BettingPros opening vs closing prices (2026, MLB)
# ---------------------------------------------------------------------------

def bp_open_close(season: int = 2026) -> dict[tuple, dict]:
    """From the captured BettingPros open+close odds, build per-game
    moneyline and total records with de-vigged fair probabilities at both
    open and close, plus the opening price to bet into. Keyed by
    (date, normalized home, normalized away)."""
    g = history.bp_game_odds(season)
    if g.empty:
        return {}
    out: dict[tuple, dict] = {}
    for _, ev in g.groupby("event_id"):
        first = ev.iloc[0]
        teams = list(first["teams"])
        if len(teams) != 2:
            continue
        home_full, away_full = teams[0], teams[1]  # parallel to abbrs[home,vis]
        date = str(first["date"])[:10]
        rec = {"date": date, "home": home_full, "away": away_full,
               "moneyline": None, "total": None}

        def side_is_home(label: str) -> bool:
            l = str(label).lower()
            return l in home_full.lower() or home_full.lower().endswith(l)

        ml = ev[ev["market_slug"] == "moneyline"]
        h = ml[ml["label"].map(side_is_home)]
        a = ml[~ml["label"].map(side_is_home)]
        if len(h) and len(a):
            ho, ao = h.iloc[0]["open_cost"], a.iloc[0]["open_cost"]
            hc, ac = h.iloc[0]["close_cost"], a.iloc[0]["close_cost"]
            hof, _ = odds.devig_two_way(odds.implied_prob(ho), odds.implied_prob(ao))
            hcf, _ = odds.devig_two_way(odds.implied_prob(hc), odds.implied_prob(ac))
            rec["moneyline"] = {
                "home_open": float(ho), "away_open": float(ao),
                "home_open_fair": float(hof), "away_open_fair": float(1 - hof),
                "home_close_fair": float(hcf), "away_close_fair": float(1 - hcf),
            }

        tot = ev[ev["market_slug"] == "total"]
        ov = tot[tot["label"].str.lower() == "over"]
        un = tot[tot["label"].str.lower() == "under"]
        if len(ov) and len(un):
            oo, uo = ov.iloc[0]["open_cost"], un.iloc[0]["open_cost"]
            oc, uc = ov.iloc[0]["close_cost"], un.iloc[0]["close_cost"]
            oof, _ = odds.devig_two_way(odds.implied_prob(oo), odds.implied_prob(uo))
            ocf, _ = odds.devig_two_way(odds.implied_prob(oc), odds.implied_prob(uc))
            rec["total"] = {
                "line": float(ov.iloc[0]["line"]),
                "over_open": float(oo), "under_open": float(uo),
                "over_open_fair": float(oof), "over_close_fair": float(ocf),
            }
        out[(date, normalize(home_full), normalize(away_full))] = rec
    return out


def run_mlb_clv_open_close(seasons: list[int] | None = None, draws: int = 4000,
                          use_starters: bool = True,
                          min_edge: float | None = None) -> dict:
    """Walk-forward MLB model, bet model edges at BettingPros *opening*
    prices, then measure (a) ROI graded on actual results and (b) CLV —
    how the de-vigged fair probability moved from open to close on the
    side we took. Positive CLV is the leading indicator that the model is
    finding real value, independent of small-sample win variance.
    """
    min_edge = config.MIN_EDGE if min_edge is None else min_edge
    seasons = seasons or [2024, 2025, 2026]
    sport = SPORTS["MLB"]
    games = _mlb_games(seasons, use_results_2026=True)
    fip_table = starter_fip_table(seasons) if use_starters else {}
    bp_table = bullpen_fip_table(seasons) if use_starters else {}
    bp = bp_open_close(2026)
    form = _Form(30)

    ml_bets, total_bets = BetLog(), BetLog()
    ml_clv, total_clv = [], []
    n_matched = 0

    for g in games:
        h = form.rating(g["home"], sport.league_ppg)
        a = form.rating(g["away"], sport.league_ppg)
        if h and a and h.games >= 10 and a.games >= 10:
            pk = g.get("game_pk")
            home_opp = fip_table.get((pk, "away")) if fip_table else None
            away_opp = fip_table.get((pk, "home")) if fip_table else None
            home_bp = bp_table.get((pk, "away")) if bp_table else None
            away_bp = bp_table.get((pk, "home")) if bp_table else None
            if use_starters:
                pf_venue, home_pf, away_pf = (parks.factor(g["home"]),
                                              parks.factor(g["home"]),
                                              parks.factor(g["away"]))
            else:
                pf_venue = home_pf = away_pf = 1.0
            hwp, _, prob_over, _ = _project("MLB", sport, h, a, draws,
                                            home_opp, away_opp, home_bp, away_bp,
                                            pf_venue, home_pf, away_pf)
            rec = bp.get((g["date"], normalize(g["home"]), normalize(g["away"])))
            home_won = 1 if g["home_score"] > g["away_score"] else 0
            actual_total = g["home_score"] + g["away_score"]
            if rec:
                n_matched += 1
                if rec["moneyline"]:
                    m = rec["moneyline"]
                    for side, prob, price, fair_o, fair_c, won in (
                        ("home", hwp, m["home_open"], m["home_open_fair"],
                         m["home_close_fair"], home_won == 1),
                        ("away", 1 - hwp, m["away_open"], m["away_open_fair"],
                         m["away_close_fair"], home_won == 0)):
                        if odds.expected_value(prob, price) >= min_edge:
                            ml_bets.add(won, odds.american_to_decimal(price))
                            ml_clv.append(fair_c - fair_o)  # +ve = moved our way
                if rec["total"]:
                    t = rec["total"]
                    po = prob_over(t["line"])
                    if actual_total != t["line"]:
                        over_won = actual_total > t["line"]
                        if odds.expected_value(po, t["over_open"]) >= min_edge:
                            total_bets.add(over_won, odds.american_to_decimal(t["over_open"]))
                            total_clv.append(t["over_close_fair"] - t["over_open_fair"])
                        if odds.expected_value(1 - po, t["under_open"]) >= min_edge:
                            total_bets.add(not over_won, odds.american_to_decimal(t["under_open"]))
                            total_clv.append((1 - t["over_close_fair"]) - (1 - t["over_open_fair"]))
        form.update(g)

    def _clv(xs):
        return round(float(np.mean(xs)), 4) if xs else None

    def _posrate(xs):
        return round(float(np.mean([x > 0 for x in xs])), 3) if xs else None

    return {
        "use_starters": use_starters, "games_matched": n_matched,
        "moneyline": {**ml_bets.summary(), "avg_clv": _clv(ml_clv),
                      "clv_positive_rate": _posrate(ml_clv)},
        "total": {**total_bets.summary(), "avg_clv": _clv(total_clv),
                  "clv_positive_rate": _posrate(total_clv)},
    }


def run_mlb_prop_calibration(seasons: list[int] | None = None,
                             min_pitcher_starts: int = 3,
                             min_batter_ab: int = 40) -> dict:
    """Walk-forward backtest of the production MLB prop models against
    actual box-score outcomes (player_games, 2024+). No lookahead: each
    projection uses only the player's prior games this season. Tests the
    real distributions in models/props.py.

    Markets: pitcher strikeouts (Poisson), batter hits (binomial), batter
    total bases (Poisson), batter home runs (P>=1). Reports projection MAE
    and a P(over) calibration table per market; the calibration gap (mean
    predicted P(over) - empirical over-rate) flags directional bias.
    """
    seasons = seasons or [2024, 2025, 2026]
    out = {}
    out["pitcher_strikeouts"] = _mlb_pitcher_k_cal(seasons, min_pitcher_starts)
    out.update(_mlb_batter_cal(seasons, min_batter_ab))
    return out


def _cal_collector():
    return {"bins": defaultdict(lambda: [0, 0.0]), "mae": [0.0, 0],
            "pred_sum": 0.0, "over_sum": 0, "n": 0}


def _cal_record(c, p_over, projection, actual, line):
    over = actual > line
    b = round(p_over * 10) / 10
    cell = c["bins"][b]
    cell[0] += 1
    cell[1] += int(over)
    c["mae"][0] += abs(projection - actual)
    c["mae"][1] += 1
    c["pred_sum"] += p_over
    c["over_sum"] += int(over)
    c["n"] += 1


def _cal_summary(c, min_bin=30):
    cal = [{"predicted": round(b, 2), "n": v[0], "empirical": round(v[1] / v[0], 4)}
           for b, v in sorted(c["bins"].items()) if v[0] >= min_bin]
    n = c["n"]
    return {
        "n": n,
        "projection_mae": round(c["mae"][0] / c["mae"][1], 3) if c["mae"][1] else None,
        "mean_pred_over": round(c["pred_sum"] / n, 4) if n else None,
        "empirical_over": round(c["over_sum"] / n, 4) if n else None,
        "calibration_gap": round((c["pred_sum"] - c["over_sum"]) / n, 4) if n else None,
        "calibration": cal,
    }


def _mlb_pitcher_k_cal(seasons, min_starts):
    pg = history.player_games("mlb", seasons=seasons)
    if pg.empty:
        return {"n": 0}
    sp = pg[(pg["position"] == "P") & (pg["started"] == True)].copy()  # noqa: E712
    sp["dt"] = pd.to_datetime(sp["date"])
    sp.sort_values("dt", inplace=True)

    cum = defaultdict(lambda: {"k": 0.0, "bf": 0.0, "ip": 0.0, "gs": 0})
    c = _cal_collector()
    for _, row in sp.iterrows():
        pid = row["player_id"]
        st = row.get("stats") or {}
        s = cum[pid]
        if s["gs"] >= min_starts and s["bf"] > 0:
            k_rate = s["k"] / s["bf"]
            exp_ip = s["ip"] / s["gs"]
            model = mlb_props.pitcher_strikeouts(exp_ip, k_rate)
            lam = model["lambda"]
            actual = st.get("strikeOuts", 0) or 0
            line = round(lam * 2) / 2
            if line == lam:
                line -= 0.5
            p_over = mlb_props.prob_over_count(lam, line)
            _cal_record(c, p_over, lam, actual, line)
        s["k"] += st.get("strikeOuts", 0) or 0
        s["bf"] += st.get("battersFaced", 0) or 0
        s["ip"] += float(st.get("inningsPitched", 0) or 0)
        s["gs"] += 1
    return _cal_summary(c)


def _mlb_batter_cal(seasons, min_ab):
    pg = history.player_games("mlb", seasons=seasons)
    if pg.empty:
        return {"batter_hits": {"n": 0}, "batter_total_bases": {"n": 0},
                "batter_home_runs": {"n": 0}}
    bat = pg[pg["position"] != "P"].copy()
    bat["dt"] = pd.to_datetime(bat["date"])
    bat.sort_values("dt", inplace=True)

    cum = defaultdict(lambda: {"h": 0.0, "tb": 0.0, "hr": 0.0, "ab": 0.0,
                               "pa": 0.0, "g": 0})
    hits_c, tb_c, hr_c = _cal_collector(), _cal_collector(), _cal_collector()
    for _, row in bat.iterrows():
        pid = row["player_id"]
        st = row.get("stats") or {}
        s = cum[pid]
        if s["ab"] >= min_ab and s["g"] >= 10:
            exp_ab = s["ab"] / s["g"]
            exp_pa = s["pa"] / s["g"]
            ba = s["h"] / s["ab"]
            slg = s["tb"] / s["ab"]
            hr_pa = s["hr"] / s["pa"] if s["pa"] else 0.0

            hits = mlb_props.batter_hits(exp_ab, ba)
            line_h = 1.5 if hits["mean"] >= 1.0 else 0.5
            p_h = mlb_props.prob_over_hits(hits["n"], hits["p"], line_h)
            _cal_record(hits_c, p_h, hits["mean"], st.get("hits", 0) or 0, line_h)

            tb = mlb_props.batter_total_bases(exp_ab, slg)
            line_tb = round(tb["lambda"] * 2) / 2
            if line_tb == tb["lambda"]:
                line_tb -= 0.5
            p_tb = mlb_props.prob_over_neg_binom(tb["lambda"], line_tb)
            _cal_record(tb_c, p_tb, tb["lambda"], st.get("totalBases", 0) or 0, line_tb)

            hr = mlb_props.batter_home_run(exp_pa, hr_pa)
            _cal_record(hr_c, hr["p_hr"], hr["p_hr"], st.get("homeRuns", 0) or 0, 0.5)

        s["h"] += st.get("hits", 0) or 0
        s["tb"] += st.get("totalBases", 0) or 0
        s["hr"] += st.get("homeRuns", 0) or 0
        s["ab"] += st.get("atBats", 0) or 0
        s["pa"] += st.get("plateAppearances", 0) or 0
        s["g"] += 1
    return {"batter_hits": _cal_summary(hits_c),
            "batter_total_bases": _cal_summary(tb_c),
            "batter_home_runs": _cal_summary(hr_c)}


def run_wnba_prop_calibration(seasons: list[int], window: int = 10,
                              min_history: int = 5) -> dict:
    """Distribution check: project each player-game as the trailing mean of
    a stat, set a book-style line near it, predict P(over) with the
    production distribution, and compare predicted vs empirical by bin.
    Validates the distribution shape, not edge (no real prop lines)."""
    df = history.player_games("wnba", seasons=seasons)
    df = df[(~df["did_not_play"]) & (df["minutes"] > 0)].copy()
    df["dt"] = pd.to_datetime(df["date"])
    df.sort_values("dt", inplace=True)

    markets = {"points": "Points", "rebounds": "Rebounds", "assists": "Assists"}
    trailing = defaultdict(lambda: defaultdict(deque))
    results = {m: defaultdict(lambda: [0, 0.0]) for m in markets}
    mae = {m: [0.0, 0] for m in markets}

    for _, row in df.iterrows():
        pid = row["player_id"]
        for stat, market_name in markets.items():
            hist = trailing[stat][pid]
            if len(hist) >= min_history:
                proj = sum(hist) / len(hist)
                line = round(proj * 2) / 2
                if line == proj:
                    line -= 0.5  # avoid a pushy whole-number == projection
                actual = row[stat]
                p_over = generic.prop_prob_over(proj, line, market_name)
                over = actual > line
                b = round(p_over * 10) / 10
                cell = results[stat][b]
                cell[0] += 1; cell[1] += int(over)
                mae[stat][0] += abs(proj - actual); mae[stat][1] += 1
            hist.append(row[stat])
            if len(hist) > window:
                hist.popleft()

    out = {}
    for stat in markets:
        cal = [{"predicted": round(b, 2), "n": c[0],
                "empirical": round(c[1] / c[0], 4)}
               for b, c in sorted(results[stat].items()) if c[0] >= 30]
        out[stat] = {
            "n": mae[stat][1],
            "projection_mae": round(mae[stat][0] / mae[stat][1], 3) if mae[stat][1] else None,
            "calibration": cal,
        }
    return out
