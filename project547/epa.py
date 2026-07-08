"""Opponent-adjusted EPA / success-rate team ratings.

The generic game model (``models/generic.py``) rates teams on points
scored/allowed. Research on the most accurate public football models
(nflfastR / nfelo / SP+ ; see docs/research/) is unanimous that the single
biggest accuracy lever is to rate teams on **opponent-adjusted EPA per play**
and **success rate** instead of raw points: points are downstream of EPA,
noisier, and conflate offense, defense, special teams and luck.

This module turns a feed of plays — each tagging the offense (``posteam``),
the defense (``defteam``) and the play's EPA / success — into per-team
offensive and defensive ratings that are adjusted for strength of schedule.
Raw EPA mostly measures *who you played*; the adjustment is mandatory.

Method: ridge regression with team dummies (the standard approach). For
offensive EPA we fit

    epa_play ~ off_team[posteam] + def_team[defteam] + intercept

with an L2 penalty so sparsely-connected teams (early season, FCS games,
disjoint conferences in NCAAF) are shrunk toward league average rather than
blowing up. The fitted offensive coefficient is a team's EPA/play added over
average vs a neutral defense; the (negated) defensive coefficient is EPA/play
prevented. Success rate is adjusted the same way.

Everything here is pure (numpy only) and walk-forward safe: feed it only plays
that occurred before the projection date. It deliberately does **not** wire
itself into the live pipeline — see docs/ACCURACY_ROADMAP.md for the staged
plan to validate it against the backtest before it touches live projections.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Plays per team-game is ~64 (NFL) / ~72 (NCAAF). Used only to express an
# EPA/play rating difference as an approximate points spread for sanity checks
# and as an optional prior into the points model.
PLAYS_PER_GAME = {"nfl": 64.0, "ncaaf": 72.0}

# Research: offense is estimated more reliably than defense; weight offense
# ~1.6x defense when collapsing the two sides into a single team strength.
OFF_DEF_WEIGHT = 1.6

# Plays in garbage time carry little predictive signal (teams play prevent
# defense / run the clock). A simple win-probability gate; callers that have a
# wp column should filter before aggregating, but we expose the threshold.
GARBAGE_WP = 0.95


@dataclass(frozen=True)
class TeamEPA:
    team: str
    off_epa: float        # opponent-adjusted offensive EPA/play (higher = better offense)
    def_epa: float        # opponent-adjusted defensive EPA/play allowed (lower = better defense)
    off_sr: float         # opponent-adjusted offensive success rate (over league mean)
    def_sr: float         # opponent-adjusted defensive success rate allowed (over league mean)
    plays: int            # offensive plays backing the estimate

    @property
    def net_epa(self) -> float:
        """Single-number team strength in EPA/play, offense weighted heavier."""
        return (OFF_DEF_WEIGHT * self.off_epa - self.def_epa) / (OFF_DEF_WEIGHT + 1.0)


def _ridge_two_way(
    off_idx: np.ndarray,
    def_idx: np.ndarray,
    y: np.ndarray,
    n_teams: int,
    lam: float,
) -> tuple[np.ndarray, np.ndarray, float]:
    """Fit y ~ intercept + off[off_idx] - def[def_idx] with an L2 penalty on the
    team effects. Returns (off_effects, def_effects, intercept).

    The defense enters with a negative sign so that a positive ``def`` effect
    means "allows more EPA than average" (a bad defense); callers read def_epa
    as EPA allowed, so lower is better. Team effects are penalized toward 0
    (league average); the intercept is unpenalized."""
    n = y.shape[0]
    # Columns: [intercept, off_0..off_{T-1}, def_0..def_{T-1}]
    p = 1 + 2 * n_teams
    X = np.zeros((n, p))
    X[:, 0] = 1.0
    rows = np.arange(n)
    X[rows, 1 + off_idx] = 1.0
    X[rows, 1 + n_teams + def_idx] = -1.0
    # Ridge penalty matrix (don't penalize the intercept).
    pen = np.full(p, lam)
    pen[0] = 0.0
    A = X.T @ X + np.diag(pen)
    b = X.T @ y
    coef = np.linalg.solve(A, b)
    intercept = float(coef[0])
    off = coef[1:1 + n_teams]
    deff = coef[1 + n_teams:]
    return off, deff, intercept


def team_epa_ratings(plays: list[dict], *, league: str = "nfl",
                     lam: float = 25.0) -> dict[str, TeamEPA]:
    """Opponent-adjusted EPA/success ratings from a list of play dicts.

    Each play must have keys ``posteam`` (offense), ``defteam`` (defense),
    ``epa`` (float). ``success`` (0/1) is optional; when absent, success is
    derived as ``epa > 0`` (the conventional definition is down-and-distance
    based, but EPA>0 is a robust proxy when a precomputed flag is unavailable).

    ``lam`` is the ridge penalty: higher = more shrinkage toward league average,
    appropriate early in the season or for sparsely-connected NCAAF schedules.
    Returns {team: TeamEPA}. Walk-forward safe — pass only prior plays.
    """
    teams = sorted({p["posteam"] for p in plays} | {p["defteam"] for p in plays})
    if not teams:
        return {}
    idx = {t: i for i, t in enumerate(teams)}
    n_teams = len(teams)

    off_idx = np.fromiter((idx[p["posteam"]] for p in plays), dtype=int, count=len(plays))
    def_idx = np.fromiter((idx[p["defteam"]] for p in plays), dtype=int, count=len(plays))
    epa = np.fromiter((float(p["epa"]) for p in plays), dtype=float, count=len(plays))
    succ = np.fromiter(
        (float(p["success"]) if "success" in p and p["success"] is not None
         else (1.0 if float(p["epa"]) > 0 else 0.0) for p in plays),
        dtype=float, count=len(plays),
    )

    off_e, def_e, _ = _ridge_two_way(off_idx, def_idx, epa, n_teams, lam)
    off_s, def_s, _ = _ridge_two_way(off_idx, def_idx, succ, n_teams, lam)

    counts = np.bincount(off_idx, minlength=n_teams)
    out: dict[str, TeamEPA] = {}
    for t, i in idx.items():
        # The ridge defense coefficient is EPA *suppressed* (higher = better
        # defense). We expose def_epa as EPA *allowed* over average (lower =
        # better defense), so negate — keeps net_epa / expected_margin and the
        # TeamEPA docstring consistent.
        out[t] = TeamEPA(
            team=t,
            off_epa=round(float(off_e[i]), 4),
            def_epa=round(float(-def_e[i]), 4),
            off_sr=round(float(off_s[i]), 4),
            def_sr=round(float(-def_s[i]), 4),
            plays=int(counts[i]),
        )
    return out


@dataclass(frozen=True)
class PasserEPA:
    passer: str
    epa: float          # opponent-adjusted pass EPA/dropback over league average
    cpoe: float         # attempt-shrunk completion % over expected (0 if unavailable)
    dropbacks: int      # dropbacks backing the estimate


def passer_epa_ratings(plays: list[dict], *, lam: float = 60.0,
                       cpoe_prior: float = 200.0) -> dict[str, PasserEPA]:
    """Opponent-adjusted per-passer pass-EPA/dropback (the QB signal research
    calls the #1 NFL lever), plus attempt-shrunk CPOE.

    Each play dict needs ``passer`` (QB id), ``defteam`` (defense) and ``epa``;
    optional ``cpoe``. Same ridge as team ratings but the offensive unit is the
    *passer*, so a QB's rating is separated from both his defense faced and (via
    the ridge shrinkage) his own small samples — early-season / backup QBs are
    pulled toward league average rather than exploding. Walk-forward safe: pass
    only dropbacks that occurred before the projection date.

    ``lam`` (higher than the team default) reflects that a single QB has far
    fewer plays than a team; ``cpoe_prior`` shrinks CPOE toward 0 by dropbacks.
    """
    passers = sorted({p["passer"] for p in plays})
    defs = sorted({p["defteam"] for p in plays})
    if not passers or not defs:
        return {}
    pidx = {t: i for i, t in enumerate(passers)}
    didx = {t: i for i, t in enumerate(defs)}
    n = len(plays)
    # Same ridge model as team ratings, but the "offense" index space is
    # passers and the "defense" space is defenses — two independent dummy
    # blocks, so the design matrix is built directly below rather than
    # through the shared team-vs-team helper.
    off_i = np.fromiter((pidx[p["passer"]] for p in plays), dtype=int, count=n)
    def_i = np.fromiter((didx[p["defteam"]] for p in plays), dtype=int, count=n)
    epa = np.fromiter((float(p["epa"]) for p in plays), dtype=float, count=n)
    # Independent dummy spaces (passers vs defenses) — build the design directly.
    npass, ndef = len(passers), len(defs)
    p_cols = 1 + npass + ndef
    X = np.zeros((n, p_cols))
    X[:, 0] = 1.0
    rows = np.arange(n)
    X[rows, 1 + off_i] = 1.0
    X[rows, 1 + npass + def_i] = -1.0
    pen = np.full(p_cols, lam)
    pen[0] = 0.0
    coef = np.linalg.solve(X.T @ X + np.diag(pen), X.T @ epa)
    pass_eff = coef[1:1 + npass]
    counts = np.bincount(off_i, minlength=npass)
    # attempt-shrunk mean CPOE per passer
    cpoe_sum: dict[int, float] = {}
    cpoe_n: dict[int, int] = {}
    for p in plays:
        c = p.get("cpoe")
        if c is not None and not (isinstance(c, float) and np.isnan(c)):
            i = pidx[p["passer"]]
            cpoe_sum[i] = cpoe_sum.get(i, 0.0) + float(c)
            cpoe_n[i] = cpoe_n.get(i, 0) + 1
    out: dict[str, PasserEPA] = {}
    for t, i in pidx.items():
        cn = cpoe_n.get(i, 0)
        cpoe = (cpoe_sum.get(i, 0.0) / cn) * (cn / (cn + cpoe_prior)) if cn else 0.0
        out[t] = PasserEPA(passer=t, epa=round(float(pass_eff[i]), 4),
                           cpoe=round(cpoe, 4), dropbacks=int(counts[i]))
    return out


def epa_to_points(epa_per_play_diff: float, league: str = "nfl") -> float:
    """Convert a net EPA/play advantage into an approximate points margin,
    using league plays-per-game. A 0.10 EPA/play edge over a full NFL game
    (~64 plays each side) is worth a meaningful spread; this gives the model a
    points-scale prior it can blend with the scoring-based ratings."""
    ppg = PLAYS_PER_GAME.get(league, 64.0)
    return round(epa_per_play_diff * ppg, 2)


def expected_margin(home: TeamEPA, away: TeamEPA, *, league: str = "nfl",
                    hfa: float = 2.0) -> float:
    """Projected home margin (points) from EPA ratings alone, before HFA blend.

    Home offense vs away defense, and away offense vs home defense, both on the
    EPA/play scale, converted to points and differenced, plus home-field edge.
    Intended as a *prior* / feature for the blended model, not a standalone
    projection — see the roadmap."""
    # def_epa is EPA *allowed* over average, so a leaky (high def_epa) opponent
    # raises the offense's expected output: home offense + away defense allowed.
    home_net = home.off_epa + away.def_epa          # home offensive edge, EPA/play
    away_net = away.off_epa + home.def_epa           # away offensive edge, EPA/play
    margin_epa = home_net - away_net
    return round(epa_to_points(margin_epa, league) + hfa, 2)
