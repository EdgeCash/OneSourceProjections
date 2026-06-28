"""First-inning run model — P(YRFI): at least one run scored in the 1st inning.

First-inning runs are lumpy (a team that scores in the 1st often scores 2-3), so
a Poisson on run counts overstates YRFI (~65% vs the real ~50%). We instead model
the BINARY event "does a team score in its half-inning?" directly and combine:

    P(YRFI) = 1 - P(no run, top 1st) * P(no run, bottom 1st)

Each half-inning's score probability combines the batting team's score-in-1st
rate with the opposing pitching's allow-in-1st rate, relative to league, via Bill
James **log5** — the standard, parameter-free way to merge an offense rate and a
defense rate against a base rate. Park and (live-only) weather then scale the run
environment. Rates are regressed to league priors because single-season
first-inning samples are noisy, and computed walk-forward (no lookahead).

Pure functions over rates, so the math unit-tests without any data; the as-of
rate tables come from :func:`team_first_inning_rates`.
"""

from __future__ import annotations

import pandas as pd

# League base rates (2025-26 game logs): a team scores in the 1st ~29.5% of the
# time; YRFI (either team scores) ~50%. These are the priors everything regresses
# toward and the league anchor for log5.
LEAGUE_SCORE_RATE = 0.295
LEAGUE_NRFI = 0.503        # P(no run in the 1st by either team)

# Regression strength: how many league-prior "games" to add to each team's
# first-inning sample. First-inning rates are noisy, so we regress hard.
OFF_PRIOR_GAMES = 45
DEF_PRIOR_GAMES = 45

# Starters allow slightly more in the 1st (facing the top of the order, not yet
# settled). A small bump to the batting side's score odds; tunable.
TOP_OF_ORDER_MULT = 1.06

# League anchors for the live-only quality signals (the specific starter and the
# confirmed top-3 hitters — the signals the team rotation/lineup average can't
# capture). Conservative blends, since these can't be backtested without
# first-inning play-by-play; forward CLV is the judge.
LEAGUE_XFIP = 4.20         # league-average starter xFIP/FIP/ERA scale
LEAGUE_WOBA = 0.318        # league-average wOBA
STARTER_BLEND = 0.65       # weight on starter-quality vs team allow-rate
TOP3_BLEND = 0.55          # weight on top-3 quality vs team offense rate
_QUAL_DAMP = 0.6           # damp the quality multiplier (first inning ≠ full game)

_EPS = 1e-4


def _clip(p: float, lo: float = _EPS, hi: float = 1 - _EPS) -> float:
    return max(lo, min(hi, float(p)))


def log5(p_off: float, p_def: float, p_league: float = LEAGUE_SCORE_RATE) -> float:
    """Combine an offense rate and a defense (allow) rate against the league
    base rate (Bill James log5). Returns the matchup-adjusted probability."""
    p_off, p_def, p_league = _clip(p_off), _clip(p_def), _clip(p_league)
    num = (p_off * p_def) / p_league
    den = num + ((1 - p_off) * (1 - p_def)) / (1 - p_league)
    return num / den if den else p_league


def scale_odds(p: float, mult: float) -> float:
    """Scale a probability's odds by ``mult`` (run-environment multiplier).
    mult>1 raises the scoring probability; mult=1 is a no-op."""
    if mult == 1.0:
        return p
    o = _clip(p) / (1 - _clip(p))
    o *= max(mult, _EPS)
    return o / (1 + o)


def regress(rate: float | None, n: int, prior: float, prior_games: int) -> float:
    """Shrink an observed rate over ``n`` games toward ``prior``."""
    if rate is None or n <= 0:
        return prior
    return (rate * n + prior * prior_games) / (n + prior_games)


def starter_allow_rate(metric: float | None, team_allow: float,
                       league: float = LEAGUE_XFIP) -> float:
    """Refine a team's allow-in-1st rate with the *specific* starter's season
    run-prevention (xFIP/FIP/ERA; lower = better). Blended with the team rate so
    it degrades gracefully and never gets overconfident off one number."""
    if metric is None or metric <= 0:
        return team_allow
    mult = (float(metric) / league) ** _QUAL_DAMP     # >1 worse, <1 better
    starter_implied = scale_odds(LEAGUE_SCORE_RATE, mult)
    return STARTER_BLEND * starter_implied + (1 - STARTER_BLEND) * team_allow


def top3_off_rate(woba: float | None, team_off: float,
                  league: float = LEAGUE_WOBA) -> float:
    """Refine a team's score-in-1st rate with the confirmed top-3 hitters' wOBA
    (the batters actually guaranteed to hit in the 1st). Blended with the team
    rate; wOBA spread is small so it nudges, not dominates."""
    if woba is None or woba <= 0:
        return team_off
    mult = (float(woba) / league) ** (1.0 / _QUAL_DAMP)   # amplify small wOBA gaps
    top3_implied = scale_odds(LEAGUE_SCORE_RATE, mult)
    return TOP3_BLEND * top3_implied + (1 - TOP3_BLEND) * team_off


def half_inning_score_prob(off_rate: float, def_allow_rate: float,
                           park: float = 1.0, weather: float = 1.0,
                           top_of_order: bool = True,
                           starter_metric: float | None = None,
                           top3_woba: float | None = None) -> float:
    """P(the batting team scores ≥1 run in this half-inning), refined by the
    specific starter (def) and confirmed top-3 hitters (off) when available."""
    off = top3_off_rate(top3_woba, off_rate)
    dfn = starter_allow_rate(starter_metric, def_allow_rate)
    p = log5(off, dfn)
    env = park * weather * (TOP_OF_ORDER_MULT if top_of_order else 1.0)
    return _clip(scale_odds(p, env), 0.02, 0.85)


def yrfi_prob(off_away: float, allow_home: float,
              off_home: float, allow_away: float,
              park: float = 1.0, weather: float = 1.0,
              home_starter: float | None = None, away_starter: float | None = None,
              away_top3_woba: float | None = None,
              home_top3_woba: float | None = None) -> dict:
    """Model the two half-innings and combine into P(YRFI). The visiting offense
    bats the top half vs the HOME starter; the home offense bats the bottom half
    vs the AWAY starter. Starter metrics / top-3 wOBA are optional refinements."""
    p_top = half_inning_score_prob(off_away, allow_home, park, weather,
                                   starter_metric=home_starter,
                                   top3_woba=away_top3_woba)   # visitor bats vs home SP
    p_bot = half_inning_score_prob(off_home, allow_away, park, weather,
                                   starter_metric=away_starter,
                                   top3_woba=home_top3_woba)   # home bats vs away SP
    p_yrfi = 1.0 - (1.0 - p_top) * (1.0 - p_bot)
    return {"p_yrfi": round(p_yrfi, 4), "p_nrfi": round(1 - p_yrfi, 4),
            "p_top": round(p_top, 4), "p_bot": round(p_bot, 4)}


# ---------------------------------------------------------------------------
# As-of first-inning team rates (walk-forward, regressed) — from the game logs
# ---------------------------------------------------------------------------
def team_first_inning_rates(df: pd.DataFrame, asof: str | None = None) -> dict:
    """{team: {"off": score-in-1st rate, "allow": allow-in-1st rate, "n": games}}
    using only games strictly before ``asof`` (no lookahead), each regressed to
    the league prior. ``df`` is the MLB team-game log (teamstats.team_games),
    one row per team-game with f1 (first-inning runs for) and opp_f1 (allowed).
    """
    if df.empty or "f1" not in df.columns or "opp_f1" not in df.columns:
        return {}
    d = df
    if asof is not None and "date" in d.columns:
        d = d[d["date"].astype(str) < str(asof)]
    out: dict = {}
    for team, g in d.groupby("team"):
        if str(team).isdigit():
            continue
        f1 = pd.to_numeric(g["f1"], errors="coerce")
        of1 = pd.to_numeric(g["opp_f1"], errors="coerce")
        scored = (f1 > 0).mean() if f1.notna().any() else None
        allowed = (of1 > 0).mean() if of1.notna().any() else None
        n = int(f1.notna().sum())
        out[team] = {
            "off": regress(scored, n, LEAGUE_SCORE_RATE, OFF_PRIOR_GAMES),
            "allow": regress(allowed, n, LEAGUE_SCORE_RATE, DEF_PRIOR_GAMES),
            "n": n,
        }
    return out
