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

import json
import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np
import pandas as pd

from . import calculators, config, history, odds, parks, teams
from .models import game as mlb_game
from .models import generic
from .models import props as mlb_props
from .models.elo import Elo, EloConfig
from .names import normalize
from .sports import SPORTS, season_label

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


# Max distance between a ±1-day closing candidate's total line and the model's
# projected total before the match is rejected as a different game. Tight for
# low-scoring (poisson) sports — an MLB series plays the same two teams on
# consecutive days and a 1.5+ run gap flags the adjacent game (different
# starter/park context); looser for normal-model sports whose model-vs-market
# total disagreement is routinely several points.
_LINE_TOL_POISSON = 1.5
_LINE_TOL_NORMAL = 10.0


def _lookup_closing(consensus: dict, sport_key: str, date: str,
                    home: str, away: str, ref_total: float | None = None
                    ) -> dict | None:
    """Find the closing record for a game, tolerating a ±1 day offset
    (closing_start is UTC, so late games roll a day past the local game
    date). Picks the closest date when several qualify.

    The ±1-day fallback (gap == 1) additionally requires the candidate's
    closing total line to be near ``ref_total`` (the model's projected total)
    when both exist — otherwise the adjacent game of an MLB/NBA/NHL series
    matches and the bet is graded against a different game's close. Among
    equal-gap candidates (a doubleheader shares one date+teams key because the
    closing store's event ids don't join to backfill game_pks) the one whose
    total line is closest to ``ref_total`` wins."""
    import datetime as _dt

    bucket = consensus.get(_team_key(sport_key, home, away))
    if not bucket:
        return None
    sp = SPORTS.get(sport_key)
    tol = _LINE_TOL_POISSON if (sp and sp.model == "poisson") else _LINE_TOL_NORMAL
    d0 = _dt.date.fromisoformat(date)

    def _line_dist(rec) -> float | None:
        line = ((rec.get("total") or {}).get("line"))
        if line is None or ref_total is None:
            return None
        return abs(float(line) - float(ref_total))

    best, best_key = None, (2, float("inf"))
    for cand_date, rec in bucket:
        gap = abs((_dt.date.fromisoformat(cand_date) - d0).days)
        if gap > 1:
            continue
        dist = _line_dist(rec)
        if gap == 1 and dist is not None and dist > tol:
            continue                    # adjacent game of a series — wrong game
        key = (gap, dist if dist is not None else float("inf"))
        if key < best_key:
            best, best_key = rec, key
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


def mlb_temp_table(seasons: list[int]) -> dict:
    """{(date 'YYYY-MM-DD', canon home team): first-pitch temp_f} from the
    committed game_context. Dome/closed-roof games map to None so the model
    skips the temperature adjustment there. Enables walk-forward validation of
    the weather effect (the live pipeline already fetches temp per game)."""
    import gzip
    from . import teams as _teams
    out: dict = {}
    for s in seasons:
        p = history.HISTORY_DIR / "backfill" / "mlb" / str(s) / "game_context.jsonl.gz"
        if not p.exists():
            continue
        with gzip.open(p, "rt") as f:
            for line in f:
                if not line.strip():
                    continue
                r = json.loads(line)
                d = str(r.get("date") or "")
                if len(d) == 8:                       # YYYYMMDD -> YYYY-MM-DD
                    d = f"{d[:4]}-{d[4:6]}-{d[6:]}"
                home = _teams.canon("MLB", r.get("home_team", ""))
                sky = (r.get("sky") or "").lower()
                temp = r.get("temp_f")
                try:
                    temp = float(temp)
                except (TypeError, ValueError):
                    temp = None
                # dome/closed roof -> no weather; guard implausible temps
                if "dome" in sky or "roof" in sky or temp is None or not (20 <= temp <= 115):
                    temp = None
                out[(d, home)] = temp
    return out


# Retrosheet wind-direction categories -> signed out/in component (+1 straight
# out to CF boosts runs, -1 straight in suppresses, corners damped, crosswind ~0).
WIND_OUT = {"tocf": 1.0, "fromcf": -1.0, "tolf": 0.6, "torf": 0.6,
            "fromlf": -0.6, "fromrf": -0.6, "ltor": 0.0, "rtol": 0.0}


def mlb_wind_table(seasons: list[int]) -> dict:
    """{(date 'YYYY-MM-DD', canon home team): (wind_out, wind_mph)} from the
    committed game_context. Dome / unknown-direction games map to (None, None) so
    the model skips the wind adjustment. Mirrors mlb_temp_table for walk-forward
    validation of the wind effect."""
    import gzip
    from . import teams as _teams
    out: dict = {}
    for s in seasons:
        p = history.HISTORY_DIR / "backfill" / "mlb" / str(s) / "game_context.jsonl.gz"
        if not p.exists():
            continue
        with gzip.open(p, "rt") as f:
            for line in f:
                if not line.strip():
                    continue
                r = json.loads(line)
                d = str(r.get("date") or "")
                if len(d) == 8:
                    d = f"{d[:4]}-{d[4:6]}-{d[6:]}"
                home = _teams.canon("MLB", r.get("home_team", ""))
                sky = (r.get("sky") or "").lower()
                wdir = (r.get("wind_dir") or "").lower()
                try:
                    mph = float(r.get("wind_speed_mph"))
                except (TypeError, ValueError):
                    mph = None
                comp = WIND_OUT.get(wdir)
                if "dome" in sky or "roof" in sky or comp is None or mph is None \
                        or not (0 <= mph <= 60):
                    out[(d, home)] = (None, None)
                else:
                    out[(d, home)] = (comp, mph)
    return out


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


def _prewarm_elo(elo: Elo, sport_key: str, first_season: int, lookback: int = 8):
    """Feed completed games from the seasons before the test window so Elo
    ratings are informed by the time grading starts."""
    warm = [s for s in range(first_season - lookback, first_season) if s >= 2002]
    if not warm:
        return
    games = _wnba_games(warm) if sport_key == "WNBA" else _generic_games(sport_key, warm)
    for g in games:
        # league season label, NOT the calendar year: cross-New-Year leagues
        # must not fire the between-season Elo regression on Jan 1 (audit #5).
        elo.update(g["home"], g["away"], g["home_score"], g["away_score"],
                   season_label(sport_key, g["date"]))


def _rest_days(team: str, gd, last_seen: dict, cap: int = 14) -> int:
    prev = last_seen.get(team)
    return cap if prev is None else min((gd - prev).days, cap)


def _generic_games(sport_key: str, seasons: list[int]) -> list[dict]:
    """Completed games for any sport with a committed backfill (NBA, NFL,
    NCAAF, NHL), keyed by full team name for the non-WNBA join."""
    df = history.backfill_games(sport_key.lower(), seasons=seasons)
    if df.empty:
        return []
    rows = []
    for _, r in df.iterrows():
        if not r.get("completed", True):
            continue
        if pd.isna(r.get("home_score")) or pd.isna(r.get("away_score")):
            continue
        rows.append({
            "date": str(r["date"])[:10], "home": r["home_team"], "away": r["away_team"],
            "home_score": r["home_score"], "away_score": r["away_score"],
            "game_pk": r.get("game_id"),
            "home_qb": r.get("home_qb"), "away_qb": r.get("away_qb"),
            "season": r.get("season"), "week": r.get("week"),
        })
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
    half_life: float = 0.0    # exponential recency half-life in games (0 = flat)
    scored: dict = field(default_factory=lambda: defaultdict(deque))
    allowed: dict = field(default_factory=lambda: defaultdict(deque))
    opp: dict = field(default_factory=lambda: defaultdict(deque))

    def _raw(self, team: str, league_ppg: float):
        s, a = self.scored[team], self.allowed[team]
        n = len(s)
        if n == 0:
            return None
        # deque is oldest→newest, matching decay_weights' ordering
        wts = generic.decay_weights(n, self.half_life)
        w = generic.RATING_SHRINK * min(1.0, n / 10)
        return (w * generic._decayed_mean(list(s), wts) + (1 - w) * league_ppg,
                w * generic._decayed_mean(list(a), wts) + (1 - w) * league_ppg, n)

    def rating(self, team: str, league_ppg: float,
               opponent_adjust: bool = False) -> generic.TeamRating | None:
        raw = self._raw(team, league_ppg)
        if raw is None:
            return None
        off, deff, n = raw
        if opponent_adjust and self.opp[team]:
            opp_raws = [self._raw(o, league_ppg) for o in self.opp[team]]
            opp_raws = [r for r in opp_raws if r]
            if opp_raws:
                # faced weak defenses (high opp 'allowed') -> discount our offense;
                # faced strong offenses (high opp 'scored') -> credit our defense.
                off += league_ppg - sum(r[1] for r in opp_raws) / len(opp_raws)
                deff += league_ppg - sum(r[0] for r in opp_raws) / len(opp_raws)
        return generic.TeamRating(games=n, scored=off, allowed=deff)

    def update(self, g: dict):
        for team, sc, al, op in ((g["home"], g["home_score"], g["away_score"], g["away"]),
                                 (g["away"], g["away_score"], g["home_score"], g["home"])):
            self.scored[team].append(sc)
            self.allowed[team].append(al)
            self.opp[team].append(op)
            if len(self.scored[team]) > self.window:
                self.scored[team].popleft()
                self.allowed[team].popleft()
                self.opp[team].popleft()


def _project(sport_key: str, sport, h: generic.TeamRating | None,
             a: generic.TeamRating | None, draws: int,
             home_opp_xfip: float | None = None,
             away_opp_xfip: float | None = None,
             home_opp_bp: float | None = None,
             away_opp_bp: float | None = None,
             park_venue: float = 1.0,
             home_own_pf: float = 1.0, away_own_pf: float = 1.0,
             temp_f: float | None = None,
             home_lineup_woba: float | None = None,
             away_lineup_woba: float | None = None,
             wind_out: float | None = None, wind_mph: float | None = None):
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
                                 park_factor=park_venue, own_home_pf=home_own_pf,
                                 temp_f=temp_f, lineup_woba=home_lineup_woba,
                                 wind_out=wind_out, wind_mph=wind_mph)
        ai = mlb_game.TeamInputs(name="a", runs_per_game=a.scored,
                                 opp_starter_xfip=away_opp_xfip,
                                 opp_bullpen_xfip=away_opp_bp,
                                 park_factor=park_venue, own_home_pf=away_own_pf,
                                 temp_f=temp_f, lineup_woba=away_lineup_woba,
                                 wind_out=wind_out, wind_mph=wind_mph)
        proj = mlb_game.simulate(hi, ai, total_lines=[], runline_spreads=[],
                                 draws=draws, seed=7)
        mu_h, mu_a = proj.home_exp_runs, proj.away_exp_runs
        # Draw from the same (negative-binomial) run distribution the live
        # simulate() uses, so the backtest grades totals/run-line on the real
        # model, not a Poisson stand-in.
        rng = np.random.default_rng(11)
        hs = mlb_game.draw_runs(rng, mu_h, draws)
        as_ = mlb_game.draw_runs(rng, mu_a, draws)
        tot, mar = hs + as_, hs - as_
        return (proj.home_win_prob, proj.total_mean,
                lambda line, _t=tot: float((_t > line).mean()),
                lambda spread, _m=mar: float((_m + spread > 0).mean()), None)
    gp = generic.project_game(sport, h, a)
    return (gp.home_win_prob, gp.total_mean,
            lambda line: gp.prob_over(line, sport),
            lambda spread: gp.home_cover_prob(spread, sport), gp)


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

def _clean_line(ln):
    if ln is None or (isinstance(ln, float) and math.isnan(ln)):
        return None
    return float(ln)


def _devig_market(books: dict, a_side: str, b_side: str):
    """books: {book: {side: (american, line)}}. Consensus honest to the line
    (audit #18/#22): group each two-sided book by its a_side line, pick the
    modal line (most two-sided books; ties -> median of the tied lines),
    de-vig each book AT that line, and average those per-book fair probs.
    Best prices per side come only from books quoting THAT line (one-sided
    books included when their quoted line matches), so an alt-line quote can
    never be graded against fair-at-a-different-line — the old max-across-
    all-lines pooling manufactured phantom EV and a free half-point/run.

    De-vig uses the **power** method: multiplicative normalization flatters
    the market baseline at the extremes (favorite-longshot bias), which would
    overstate our edge against it.

    Returns (fair_a, line, best_a, best_b) or None when no book has both
    sides. line is None for unlined markets (moneylines)."""
    two_sided: dict = defaultdict(list)   # line -> [(fair_a, am_a, am_b)]
    a_only: list = []                     # (line, american) one-sided quotes
    b_only: list = []
    for sides in books.values():
        la = _clean_line(sides[a_side][1]) if a_side in sides else None
        lb = _clean_line(sides[b_side][1]) if b_side in sides else None
        if a_side in sides and b_side in sides:
            # both sides must describe the same market: identical line
            # (totals) or mirrored line (spreads quoted per side).
            if la is not None and lb is not None and la != lb and la != -lb:
                continue
            am_a, am_b = sides[a_side][0], sides[b_side][0]
            pa, pb = odds.implied_prob(am_a), odds.implied_prob(am_b)
            if not (0.98 <= pa + pb <= 1.30):
                continue                     # stale/mismatched pair
            fa, _ = calculators.no_vig(am_a, am_b, method="power")
            two_sided[la].append((fa, float(am_a), float(am_b)))
        elif a_side in sides:
            a_only.append((la, float(sides[a_side][0])))
        elif b_side in sides:
            b_only.append((lb, float(sides[b_side][0])))
    if not two_sided:
        return None
    # modal a_side line among two-sided books; ties -> median of tied lines
    top = max(len(v) for v in two_sided.values())
    tied = sorted((l for l, v in two_sided.items() if len(v) == top),
                  key=lambda l: (l is None, l))
    line = tied[len(tied) // 2]
    entries = two_sided[line]
    fair_a = sum(e[0] for e in entries) / len(entries)
    a_ams = [e[1] for e in entries] + [am for l, am in a_only
                                       if l == line or l is None or line is None]
    b_ams = [e[2] for e in entries] + [am for l, am in b_only
                                       if l == line or l == (-line if line is not None else None)
                                       or l is None or line is None]
    return fair_a, line, float(max(a_ams)), float(max(b_ams))


@lru_cache(maxsize=8)
def closing_consensus(sport: str) -> dict[tuple, dict]:
    """Keyed by the sport's _join_key -> consensus closing market data:
    de-vigged fair probs + best available prices per side. Pre-groups rows by
    event once and processes in pure Python (pandas-per-event was ~80s on the
    7.5k-game NBA store); cached per sport for repeated backtests/sweeps."""
    cl = history.closing_lines(sport)
    if cl.empty:
        return {}
    # drop malformed prices: real American odds are always |odds| >= 100
    # (some legacy NBA/NHL rows carry junk like 5.85, which crashes the de-vig).
    cl = cl.assign(american_odds=pd.to_numeric(cl["american_odds"], errors="coerce"))
    cl = cl[cl["american_odds"].abs() >= 100]
    if cl.empty:
        return {}
    cl = cl.assign(date=cl["scheduled_start"].astype(str).str[:10])  # UTC date; ±1d
    cl = cl.sort_values("captured_at").drop_duplicates(
        ["event_id", "book", "market", "side"], keep="last")

    cols = ["event_id", "book", "market", "side", "american_odds", "line",
            "date", "home_team", "away_team"]
    ev_rows: dict = defaultdict(list)
    for r in cl[cols].itertuples(index=False, name=None):
        ev_rows[r[0]].append(r)

    out: dict[tuple, list] = defaultdict(list)
    for eid, rows in ev_rows.items():
        date, home, away = rows[0][6], rows[0][7], rows[0][8]
        # event_id disambiguates doubleheaders (same date + teams). It doesn't
        # join to the backfill's game_pk, so _lookup_closing falls back to
        # line proximity, but the id is recorded for any caller that can join.
        rec = {"date": date, "event_id": eid, "home": home, "away": away,
               "moneyline": None, "total": None, "spread": None}
        markets: dict = defaultdict(lambda: defaultdict(dict))
        for (_e, book, market, side, am_, line, _d, _h, _a) in rows:
            markets[market][book][side] = (am_, line)

        ml = _devig_market(markets.get("moneyline", {}), "home", "away")
        if ml:
            fa, _ln, hb, ab = ml
            rec["moneyline"] = {"home_fair": fa, "away_fair": 1 - fa,
                                "home_best": hb, "away_best": ab}
        tt = _devig_market(markets.get("total", {}), "over", "under")
        if tt:
            fo, ln, ob, ub = tt
            rec["total"] = {"line": ln, "over_fair": fo,
                            "over_best": ob, "under_best": ub}
        sp = _devig_market(markets.get("spread", {}), "home", "away")
        if sp:
            fh, ln, hb, ab = sp
            rec["spread"] = {"line": ln, "home_fair": fh,
                             "home_best": hb, "away_best": ab}
        out[_team_key(sport, home, away)].append((date, rec))
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
    clv_sum: float = 0.0
    clv_sq: float = 0.0
    clv_n: int = 0

    def add(self, won: bool, dec_odds: float, clv: float | None = None):
        """clv (optional): the bet's own-side EV at the closing fair prob —
        genuine per-bet CLV (positive = the price taken beats the no-vig
        close), the same convention as clv.clv_pct / run_mlb_clv_open_close."""
        self.n += 1
        self.staked += 1.0
        if won:
            self.wins += 1
            self.pnl += dec_odds - 1.0
        else:
            self.pnl -= 1.0
        if clv is not None:
            self.clv_sum += clv
            self.clv_sq += clv * clv
            self.clv_n += 1

    def clv_stats(self) -> dict:
        """Per-bet CLV mean, sample std, and one-sided lower bound — the same
        (avg_clv, clv_sd, clv_lb) shape edge_gate consumes, so a backtest run can
        seed the curation gate (docs/CURATION_DESIGN.md step 2)."""
        import math
        n = self.clv_n
        if not n:
            return {"clv_n": 0, "avg_clv": None, "clv_sd": None, "clv_lb": None}
        avg = self.clv_sum / n
        sd = lb = None
        if n >= 2:
            var = max(0.0, (self.clv_sq - n * avg * avg) / (n - 1))
            sd = math.sqrt(var)
            lb = avg - 1.28 * sd / math.sqrt(n)   # GATE_Z; one-sided ~90% bound
        return {"clv_n": n, "avg_clv": round(avg, 4),
                "clv_sd": round(sd, 4) if sd is not None else None,
                "clv_lb": round(lb, 4) if lb is not None else None}

    def summary(self) -> dict:
        return {"bets": self.n, "win_rate": round(self.wins / self.n, 4) if self.n else None,
                "units": round(self.pnl, 2),
                "roi_pct": round(100 * self.pnl / self.staked, 2) if self.staked else None,
                "avg_bet_clv": (round(self.clv_sum / self.clv_n, 4)
                                if self.clv_n else None)}


def _combined_bet_clv(*logs: "BetLog") -> float | None:
    """Average per-bet CLV pooled across bet logs (ML+total+spread)."""
    n = sum(l.clv_n for l in logs)
    return round(sum(l.clv_sum for l in logs) / n, 4) if n else None


def _band_clv(ev_sel: float, fair: float, price: float) -> float | None:
    """Per-bet CLV (own-side EV at the closing fair), but only for bets in the
    curated EV band (ev < STALE_EV). Stale-line best-of-book longshots are still
    bet for ROI but excluded from the CLV mean — the same population the live
    edge_gate.market_stats scores, so the two are comparable (and a backtest run
    can seed the gate). Returns None outside the band."""
    if ev_sel >= config.STALE_EV:
        return None
    return odds.expected_value(fair, price)


def _total_crps(prob_over, mean: float, actual: float) -> float:
    """Discrete CRPS of the model's *total* distribution against the observed
    total, using the model's own P(total > line) curve as the predictive CDF.

    CRPS = ∫ (F(x) - 1{x >= actual})^2 dx, F(x) = P(total <= x) = 1 - prob_over(x).
    Lower is better; unlike MAE/RMSE it rewards getting the *spread* of the total
    right, not just the mean — the metric that tells us whether distribution-shape
    work (over-dispersion, key-number spikes) actually helped. Evaluated on a
    bounded, mean-centred grid (≤ ~80 points) so it stays cheap inside the loop.
    """
    half = max(abs(actual - mean) + 2.0, 0.5 * mean + 8.0)
    lo = max(0.0, mean - half)
    hi = mean + half
    n = min(80, max(8, int(hi - lo) + 1))
    step = (hi - lo) / (n - 1)
    crps = 0.0
    for i in range(n):
        x = lo + i * step
        f = 1.0 - prob_over(x)                 # P(total <= x)
        indicator = 1.0 if x >= actual else 0.0
        crps += (f - indicator) ** 2 * step
    return crps


def run_game_backtest(sport_key: str, seasons: list[int], min_games: int = 10,
                      draws: int = 4000, min_edge: float | None = None,
                      use_starters: bool = False, use_bullpen: bool = False,
                      use_park: bool = False, use_temp: bool = False,
                      shrink: float | None = None,
                      apply_calibration: bool = False,
                      production_mode: bool = False,
                      rest_coeff: float | None = None,
                      opponent_adjust: bool | None = None,
                      qb_coeff: float | None = None,
                      elo_k: float | None = None,
                      elo_regress: float | None = None,
                      elo_home_edge: float | None = None,
                      elo_blend: float | None = None,
                      sigma_margin: float | None = None,
                      sigma_total: float | None = None,
                      form_half_life: float | None = None,
                      lineup_woba_table: dict | None = None,
                      lineup_blend: float | None = None,
                      use_wind: bool = False, wind_coef: float | None = None,
                      detail: bool = False) -> dict:
    import dataclasses as _dc
    min_edge = config.MIN_EDGE if min_edge is None else min_edge
    sport = SPORTS[sport_key]
    # Reproduce the deployed pipeline (audit #20): production maps the raw
    # model prob through the fitted per-(sport, market) calibration and then
    # shrinks it toward the de-vigged close by sport.market_shrink before
    # computing EV (pipeline._market_eval). production_mode=True turns both
    # on; both default OFF so historical backtest numbers are unchanged.
    # ``shrink=None`` means "the default": 0.0 (raw model), or the sport's
    # production market_shrink under production_mode. Pass a float to sweep.
    if production_mode:
        apply_calibration = True
    if shrink is None:
        shrink = sport.market_shrink if production_mode else 0.0
    if apply_calibration:
        from . import calibrate as _calmod   # lazy import, like the pipeline
        _cal = _calmod.calibrate
    else:
        _cal = lambda p, _s, _m: p           # noqa: E731 — identity when off
    # Optional lineup-level offense (roadmap T2.2): temporarily set the global
    # blend for this run so _project's TeamInputs pick it up; restored in finally.
    _saved_blend = config.LINEUP_BLEND
    if lineup_blend is not None:
        config.LINEUP_BLEND = lineup_blend
    _saved_wind = config.WIND_COEF
    if wind_coef is not None:
        config.WIND_COEF = wind_coef
    # sigma overrides for calibration sweeps (frozen dataclass -> replace).
    if sigma_margin is not None or sigma_total is not None:
        sport = _dc.replace(
            sport,
            sigma_margin=sport.sigma_margin if sigma_margin is None else sigma_margin,
            sigma_total=sport.sigma_total if sigma_total is None else sigma_total)
    rest_coeff = sport.rest_coeff if rest_coeff is None else rest_coeff
    qb_coeff = sport.qb_coeff if qb_coeff is None else qb_coeff
    if opponent_adjust is None:
        opponent_adjust = sport.opponent_adjust
    # Elo params: default to the sport config (so the backtest models what
    # production actually runs — the live pipeline builds Elo from sport.elo_*),
    # override to sweep. Previously this used a bare Elo() with library defaults,
    # so it silently validated k=16/regress=0.25/home_edge=50 regardless of the
    # sport's configured values.
    elo_k = sport.elo_k if elo_k is None else elo_k
    elo_regress = sport.elo_regress if elo_regress is None else elo_regress
    elo_home_edge = sport.elo_home_edge if elo_home_edge is None else elo_home_edge
    elo_blend = sport.elo_blend if elo_blend is None else elo_blend
    form_half_life = sport.form_half_life if form_half_life is None else form_half_life
    games = (_mlb_games(seasons, use_results_2026=True) if sport_key == "MLB"
             else _wnba_games(seasons) if sport_key == "WNBA"
             else _generic_games(sport_key, seasons))
    window = 30 if sport_key == "MLB" else 15
    form = _Form(window, half_life=form_half_life)
    last_seen: dict = {}                  # team -> last game date, for rest days
    qb_value: dict = {}                   # qb id -> EMA of team points started
    qb_base: dict = {}                    # team -> EMA of its starters' qb_value
    consensus = closing_consensus(sport_key)
    is_mlb = sport_key == "MLB"
    fip_table = starter_fip_table(seasons) if (is_mlb and use_starters) else {}
    temp_tbl = mlb_temp_table(seasons) if (is_mlb and use_temp) else {}
    wind_tbl = mlb_wind_table(seasons) if (is_mlb and use_wind) else {}
    bp_table = bullpen_fip_table(seasons) if (is_mlb and use_bullpen) else {}

    # Elo (generic sports): maintain ratings walk-forward, pre-warmed from
    # seasons before the test window so early-window ratings are informed.
    elo = None
    if not is_mlb and elo_blend > 0:
        elo = Elo(EloConfig(k=elo_k, home_edge=elo_home_edge,
                            season_regress=elo_regress))
        _prewarm_elo(elo, sport_key, min(seasons))

    # accuracy accumulators
    brier_sum = logloss_sum = n_ml = 0.0
    fav_correct = 0
    total_abs = total_sq = n_tot = 0.0
    cal_bins = defaultdict(lambda: [0, 0.0])  # bin -> [count, wins]
    # market-baseline skill: score the de-vigged close on the *same* matched
    # games the model is scored on, so "does the model beat free information?"
    # is answered apples-to-apples (the north-star question — cross-sport #2).
    mkt_brier_sum = mkt_logloss_sum = n_ml_mkt = 0.0
    mdl_brier_mkt_sum = mdl_logloss_mkt_sum = 0.0
    mkt_tot_abs = mkt_tot_sq = mdl_tot_abs_mkt = mdl_tot_sq_mkt = n_tot_mkt = 0.0
    crps_sum = 0.0
    # betting accumulators
    ml_bets, total_bets, spread_bets = BetLog(), BetLog(), BetLog()
    # signed home-side model-vs-market disagreement per matched game. This is
    # NOT CLV (a model with huge symmetric noise averages ~0 here) — it's a
    # directional bias diagnostic, reported as home_prob_bias (audit #19).
    # Genuine per-bet CLV lives in each BetLog (avg_bet_clv).
    home_bias, spread_home_bias = [], []
    detail_rows: list = []
    n_matched = 0
    n_with_starter = 0

    for g in games:
        h = form.rating(g["home"], sport.league_ppg, opponent_adjust)
        a = form.rating(g["away"], sport.league_ppg, opponent_adjust)
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
            temp_f = temp_tbl.get((g["date"], teams.canon("MLB", g["home"]))) \
                if temp_tbl else None
            wind_out, wind_mph = wind_tbl.get(
                (g["date"], teams.canon("MLB", g["home"])), (None, None)) \
                if wind_tbl else (None, None)
            lw = lineup_woba_table or {}
            hwp, tmean, prob_over, cover_fn, gp_obj = _project(
                sport_key, sport, h, a, draws, home_opp, away_opp,
                home_bp, away_bp, pf_venue, home_pf, away_pf, temp_f=temp_f,
                home_lineup_woba=lw.get((pk, "home")),
                away_lineup_woba=lw.get((pk, "away")),
                wind_out=wind_out, wind_mph=wind_mph)
            if elo is not None:
                ewp = elo.home_win_prob(g["home"], g["away"],
                                        season_label(sport_key, g["date"]))
                hwp = (1 - elo_blend) * hwp + elo_blend * ewp
            if rest_coeff and sport.sigma_margin > 0:
                from datetime import date as _D
                gd = _D.fromisoformat(g["date"])
                rest_diff = (_rest_days(g["home"], gd, last_seen)
                             - _rest_days(g["away"], gd, last_seen))
                hwp = generic.shift_win_prob(hwp, rest_coeff * rest_diff,
                                             sport.sigma_margin)
            if (qb_coeff and sport.sigma_margin > 0
                    and g.get("home_qb") and g.get("away_qb")):
                lg = sport.league_ppg
                h_dev = (qb_value.get(g["home_qb"], lg) - qb_base.get(g["home"], lg))
                a_dev = (qb_value.get(g["away_qb"], lg) - qb_base.get(g["away"], lg))
                hwp = generic.shift_win_prob(hwp, qb_coeff * (h_dev - a_dev),
                                             sport.sigma_margin)
            # Rebuild the spread cover from the fully-adjusted win prob so the
            # backtest grades spreads with a margin consistent with the
            # moneyline — mirroring the live pipeline's with_consistent_margin.
            if gp_obj is not None and sport.model == "normal" and sport.sigma_margin > 0:
                gp_obj = generic.with_consistent_margin(gp_obj, hwp, sport)
                cover_fn = lambda spread, _g=gp_obj: _g.home_cover_prob(spread, sport)
            tie = g["home_score"] == g["away_score"]
            home_won = 1 if g["home_score"] > g["away_score"] else 0
            actual_total = g["home_score"] + g["away_score"]

            # ML accuracy — ties excluded (audit #22): a tie is neither a home
            # nor an away win, so it carries no two-way Brier/calibration
            # information, and books push two-way moneylines on it.
            p = min(max(hwp, 1e-6), 1 - 1e-6)
            if not tie:
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

            # betting vs closing (ref_total keeps the ±1-day fallback off the
            # adjacent game of a series / the other half of a doubleheader)
            rec = _lookup_closing(consensus, sport_key, g["date"],
                                  g["home"], g["away"], ref_total=tmean)
            if rec:
                n_matched += 1
                if rec["moneyline"]:
                    m = rec["moneyline"]
                    home_bias.append(hwp - m["home_fair"])
                    if not tie:
                        # market baseline vs model on this matched game (raw model
                        # prob, pre bet-selection shrink — that's what we're judging)
                        mfair = min(max(m["home_fair"], 1e-6), 1 - 1e-6)
                        mkt_brier_sum += (mfair - home_won) ** 2
                        mkt_logloss_sum += -(home_won * math.log(mfair)
                                             + (1 - home_won) * math.log(1 - mfair))
                        mdl_brier_mkt_sum += (p - home_won) ** 2
                        mdl_logloss_mkt_sum += -(home_won * math.log(p)
                                                 + (1 - home_won) * math.log(1 - p))
                        n_ml_mkt += 1
                        # calibrate (production_mode) then blend the model prob
                        # toward the de-vigged closing line before deciding bets
                        # (defaults: raw model prob, shrink=0)
                        p_home = odds.blend_toward_market(
                            _cal(hwp, sport_key, "moneyline"), m["home_fair"], shrink)
                        for side, prob, price, fair, won in (
                            ("home", p_home, m["home_best"], m["home_fair"], home_won == 1),
                            ("away", 1 - p_home, m["away_best"], 1 - m["home_fair"], home_won == 0)):
                            ev_sel = odds.expected_value(prob, price)
                            if ev_sel >= min_edge:
                                # per-bet CLV: the taken price's EV at the closing
                                # fair prob of the side taken — logged only for the
                                # curated band (ev < STALE_EV), matching
                                # edge_gate.market_stats, so stale-line best-of-book
                                # longshots don't dominate the CLV mean.
                                ml_bets.add(won, odds.american_to_decimal(price),
                                            clv=_band_clv(ev_sel, fair, price))
                if rec["total"]:
                    t = rec["total"]
                    # market baseline for the total = the closing total line
                    # itself; compare our projected total against it on the same
                    # games, plus a distributional CRPS of our total.
                    mkt_tot_abs += abs(t["line"] - actual_total)
                    mkt_tot_sq += (t["line"] - actual_total) ** 2
                    mdl_tot_abs_mkt += abs(tmean - actual_total)
                    mdl_tot_sq_mkt += (tmean - actual_total) ** 2
                    crps_sum += _total_crps(prob_over, tmean, actual_total)
                    n_tot_mkt += 1
                    po = prob_over(t["line"])
                    p_over = odds.blend_toward_market(
                        _cal(po, sport_key, "total"), t["over_fair"], shrink)
                    over_won = actual_total > t["line"]
                    push = actual_total == t["line"]
                    if not push:
                        ev_o = odds.expected_value(p_over, t["over_best"])
                        if ev_o >= min_edge:
                            total_bets.add(over_won, odds.american_to_decimal(t["over_best"]),
                                           clv=_band_clv(ev_o, t["over_fair"], t["over_best"]))
                        ev_u = odds.expected_value(1 - p_over, t["under_best"])
                        if ev_u >= min_edge:
                            total_bets.add(not over_won, odds.american_to_decimal(t["under_best"]),
                                           clv=_band_clv(ev_u, 1 - t["over_fair"], t["under_best"]))
                if rec["spread"]:
                    s = rec["spread"]
                    pc = cover_fn(s["line"])           # model P(home covers home line)
                    spread_home_bias.append(pc - s["home_fair"])
                    p_cover = odds.blend_toward_market(
                        _cal(pc, sport_key, "spread"), s["home_fair"], shrink)
                    margin = g["home_score"] - g["away_score"]
                    home_cover = (margin + s["line"]) > 0
                    push = (margin + s["line"]) == 0
                    if not push:
                        ev_h = odds.expected_value(p_cover, s["home_best"])
                        if ev_h >= min_edge:
                            spread_bets.add(home_cover, odds.american_to_decimal(s["home_best"]),
                                            clv=_band_clv(ev_h, s["home_fair"], s["home_best"]))
                        ev_a = odds.expected_value(1 - p_cover, s["away_best"])
                        if ev_a >= min_edge:
                            spread_bets.add(not home_cover, odds.american_to_decimal(s["away_best"]),
                                            clv=_band_clv(ev_a, 1 - s["home_fair"], s["away_best"]))
            if detail:
                sp_ = (rec or {}).get("spread") or {}
                to_ = (rec or {}).get("total") or {}
                mo_ = (rec or {}).get("moneyline") or {}
                d = {"date": g["date"], "season": g.get("season"), "week": g.get("week"),
                     "game_pk": g.get("game_pk"),
                     "home": g["home"], "away": g["away"],
                     "home_score": g["home_score"], "away_score": g["away_score"],
                     "home_win_prob": round(hwp, 4), "proj_total": round(tmean, 2),
                     "close_spread": sp_.get("line"), "close_total": to_.get("line"),
                     "home_ml": mo_.get("home_best"), "away_ml": mo_.get("away_best"),
                     "ml_fav": g["home"] if hwp >= 0.5 else g["away"],
                     # a tied final grades no ML pick (books push)
                     "ml_hit": (None if tie else (home_won == 1) == (hwp >= 0.5)),
                     # signed model-vs-close disagreement — bias, not CLV
                     "home_prob_bias": (round(hwp - mo_["home_fair"], 4)
                                        if mo_.get("home_fair") is not None else None)}
                d["home_won"] = None if tie else int(home_won)
                if sp_.get("line") is not None:
                    pc = cover_fn(sp_["line"])
                    margin = g["home_score"] - g["away_score"]
                    d["ats_pick"] = g["home"] if pc >= 0.5 else g["away"]
                    d["ats_hit"] = (None if (margin + sp_["line"]) == 0
                                    else ((margin + sp_["line"] > 0) == (pc >= 0.5)))
                    # raw (pred, outcome) for calibration fitting/validation
                    d["p_cover"] = round(float(pc), 4)
                    d["cover_won"] = (None if (margin + sp_["line"]) == 0
                                      else int(margin + sp_["line"] > 0))
                if to_.get("line") is not None:
                    po = prob_over(to_["line"])
                    d["tot_pick"] = "Over" if po >= 0.5 else "Under"
                    d["tot_hit"] = (None if actual_total == to_["line"]
                                    else ((actual_total > to_["line"]) == (po >= 0.5)))
                    d["p_over"] = round(float(po), 4)
                    d["over_won"] = (None if actual_total == to_["line"]
                                     else int(actual_total > to_["line"]))
                detail_rows.append(d)
        form.update(g)
        from datetime import date as _D
        last_seen[g["home"]] = last_seen[g["away"]] = _D.fromisoformat(g["date"])
        lg = sport.league_ppg
        for qb, team, pts in ((g.get("home_qb"), g["home"], g["home_score"]),
                              (g.get("away_qb"), g["away"], g["away_score"])):
            if not qb:
                continue
            qb_value[qb] = 0.8 * qb_value.get(qb, lg) + 0.2 * pts
            qb_base[team] = 0.85 * qb_base.get(team, lg) + 0.15 * qb_value[qb]
        if elo is not None:
            elo.update(g["home"], g["away"], g["home_score"], g["away_score"],
                       season_label(sport_key, g["date"]))

    config.LINEUP_BLEND = _saved_blend      # restore any per-run blend override
    config.WIND_COEF = _saved_wind
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
        "market_baseline": {
            # Does the model beat the de-vigged close (free information) on the
            # same games? Skill score > 0 => model beats the market baseline.
            "games_matched_ml": int(n_ml_mkt),
            "market_brier": round(mkt_brier_sum / n_ml_mkt, 4) if n_ml_mkt else None,
            "model_brier_matched": round(mdl_brier_mkt_sum / n_ml_mkt, 4) if n_ml_mkt else None,
            "brier_skill_score": (round(1 - mdl_brier_mkt_sum / mkt_brier_sum, 4)
                                  if mkt_brier_sum else None),
            "market_log_loss": round(mkt_logloss_sum / n_ml_mkt, 4) if n_ml_mkt else None,
            "model_log_loss_matched": round(mdl_logloss_mkt_sum / n_ml_mkt, 4) if n_ml_mkt else None,
            "games_matched_total": int(n_tot_mkt),
            "market_total_mae": round(mkt_tot_abs / n_tot_mkt, 3) if n_tot_mkt else None,
            "model_total_mae_matched": round(mdl_tot_abs_mkt / n_tot_mkt, 3) if n_tot_mkt else None,
            "market_total_rmse": round(math.sqrt(mkt_tot_sq / n_tot_mkt), 3) if n_tot_mkt else None,
            "model_total_rmse_matched": round(math.sqrt(mdl_tot_sq_mkt / n_tot_mkt), 3) if n_tot_mkt else None,
            # +ve => our projected total is closer than the closing line
            "total_mae_skill": (round((mkt_tot_abs - mdl_tot_abs_mkt) / n_tot_mkt, 3)
                                if n_tot_mkt else None),
            "model_total_crps": round(crps_sum / n_tot_mkt, 3) if n_tot_mkt else None,
        },
        "production_mode": production_mode,
        "apply_calibration": apply_calibration,
        "shrink": shrink,
        "closing_line": {
            "games_matched": n_matched,
            # signed home-side model-vs-market disagreement (bias diagnostic),
            # formerly mislabeled avg_clv_vs_fair (audit #19). Real per-bet CLV
            # is avg_bet_clv inside each *_bets summary.
            "home_prob_bias": round(float(np.mean(home_bias)), 4) if home_bias else None,
            "spread_home_prob_bias": (round(float(np.mean(spread_home_bias)), 4)
                                      if spread_home_bias else None),
            "avg_bet_clv": _combined_bet_clv(ml_bets, total_bets, spread_bets),
            "moneyline_bets": ml_bets.summary(),
            "total_bets": total_bets.summary(),
            "spread_bets": spread_bets.summary(),
            # per-(market) CLV mean/sd/lb — the shape edge_gate consumes, so a
            # production-mode run can seed the curation gate (CURATION_DESIGN
            # step 2). Keyed by the gate's market names.
            "clv_by_market": {
                "moneyline": ml_bets.clv_stats(),
                "total": total_bets.clv_stats(),
                "spread": spread_bets.clv_stats(),
            },
        },
        "games": detail_rows,
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
            # power de-vig (audit #22): multiplicative flatters the market
            # baseline at the extremes, overstating open→close CLV on dogs.
            hof, _ = calculators.no_vig(float(ho), float(ao), method="power")
            hcf, _ = calculators.no_vig(float(hc), float(ac), method="power")
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
            oof, _ = calculators.no_vig(float(oo), float(uo), method="power")
            ocf, _ = calculators.no_vig(float(oc), float(uc), method="power")
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
            hwp, _, prob_over, _, _ = _project("MLB", sport, h, a, draws,
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
    coll = {m: _cal_collector() for m in markets}

    for _, row in df.iterrows():
        pid = row["player_id"]
        for stat, market_name in markets.items():
            hist = trailing[stat][pid]
            if len(hist) >= min_history:
                proj = sum(hist) / len(hist)
                line = round(proj * 2) / 2
                if line == proj:
                    line -= 0.5  # avoid a pushy whole-number == projection
                if line >= 0.5:  # skip degenerate sub-0.5 lines (no real market)
                    p_over = generic.prop_prob_over(proj, line, market_name)
                    _cal_record(coll[stat], p_over, proj, row[stat], line)
            hist.append(row[stat])
            if len(hist) > window:
                hist.popleft()

    return {stat: _cal_summary(coll[stat]) for stat in markets}
