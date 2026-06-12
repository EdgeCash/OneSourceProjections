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

from . import config, history, odds
from .models import game as mlb_game
from .models import generic
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
    """date, home, away, home_score, away_score. Uses full-name results for
    2026 (joins to closing lines) and abbreviated backfill otherwise."""
    rows = []
    for s in seasons:
        if s == 2026 and use_results_2026:
            df = history.results("mlb", 2026)
            for _, r in df.iterrows():
                if r.get("status") not in (None, "final", "Final"):
                    continue
                rows.append({
                    "date": str(r["date"])[:10], "home": r["home_team"],
                    "away": r["away_team"], "home_score": r["home_score"],
                    "away_score": r["away_score"], "home_nick": _nick_from_full(r["home_team"]),
                    "away_nick": _nick_from_full(r["away_team"]),
                })
        else:
            df = history.backfill_games("mlb", seasons=[s])
            for _, r in df.iterrows():
                rows.append({
                    "date": str(r["date"])[:10], "home": r["home_team"],
                    "away": r["away_team"], "home_score": r["home_score"],
                    "away_score": r["away_score"], "home_nick": None, "away_nick": None,
                })
    rows = [r for r in rows if pd.notna(r["home_score"]) and pd.notna(r["away_score"])]
    rows.sort(key=lambda r: r["date"])
    return rows


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
             a: generic.TeamRating | None, draws: int):
    """Return (home_win_prob, total_mean, prob_over_fn, home_cover_fn)."""
    if sport_key == "MLB":
        # Production Monte-Carlo model, offense-only (no starter).
        hi = mlb_game.TeamInputs(name="h", runs_per_game=h.scored, opp_starter_xfip=None)
        ai = mlb_game.TeamInputs(name="a", runs_per_game=a.scored, opp_starter_xfip=None)
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
                      draws: int = 4000, min_edge: float | None = None) -> dict:
    min_edge = config.MIN_EDGE if min_edge is None else min_edge
    sport = SPORTS[sport_key]
    games = (_mlb_games(seasons, use_results_2026=True) if sport_key == "MLB"
             else _wnba_games(seasons))
    window = 30 if sport_key == "MLB" else 15
    form = _Form(window)
    consensus = closing_consensus(sport_key)

    # accuracy accumulators
    brier_sum = logloss_sum = n_ml = 0.0
    fav_correct = 0
    total_abs = total_sq = n_tot = 0.0
    cal_bins = defaultdict(lambda: [0, 0.0])  # bin -> [count, wins]
    # betting accumulators
    ml_bets, total_bets = BetLog(), BetLog()
    clv_deltas = []
    n_matched = 0

    for g in games:
        h = form.rating(g["home"], sport.league_ppg)
        a = form.rating(g["away"], sport.league_ppg)
        if h and a and h.games >= min_games and a.games >= min_games:
            hwp, tmean, prob_over, _ = _project(sport_key, sport, h, a, draws)
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
