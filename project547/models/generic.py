"""Generic cross-sport models.

Game model: team offensive/defensive ratings from recent scores, shrunk
toward league average, then either
  - normal margin/total (NBA, WNBA, NFL, NCAAF), or
  - Poisson simulation (NHL — same machinery as the MLB game model).

Prop model: a probability distribution wrapped around a projected stat.
With no per-sport stat pipelines (yet), projections come from blending
FantasyPros and BettingPros' premium projection; the distribution choice
turns that point estimate into P(over):
  - small counts (proj < 8: rebounds, assists, goals, threes, TDs) → Poisson
  - yardage markets → Normal, sd = 0.25 * proj + 10
  - everything else (points, saves, attempts) → Normal, sd = 0.25 * proj + 1.5
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import stats

from ..sports import Sport

RATING_SHRINK = 0.65  # weight on observed rate vs league average


@dataclass
class TeamRating:
    games: int
    scored: float   # points per game, shrunk
    allowed: float


def team_ratings(results: list[dict], league_ppg: float,
                 opponent_adjust: bool = False) -> dict[str, TeamRating]:
    """results: [{home_team, away_team, home_score, away_score}, ...].

    When opponent_adjust is set, the off/def rates get a one-pass
    strength-of-schedule correction: facing weak defenses discounts your
    offense, facing strong offenses credits your defense."""
    raw: dict[str, list[tuple[float, float]]] = {}
    opps: dict[str, list[str]] = {}
    for g in results:
        raw.setdefault(g["home_team"], []).append((g["home_score"], g["away_score"]))
        raw.setdefault(g["away_team"], []).append((g["away_score"], g["home_score"]))
        opps.setdefault(g["home_team"], []).append(g["away_team"])
        opps.setdefault(g["away_team"], []).append(g["home_team"])

    base = {}
    for team, games in raw.items():
        n = len(games)
        w = RATING_SHRINK * min(1.0, n / 10)
        base[team] = (w * (sum(s for s, _ in games) / n) + (1 - w) * league_ppg,
                      w * (sum(a for _, a in games) / n) + (1 - w) * league_ppg, n)

    out = {}
    for team, (scored, allowed, n) in base.items():
        if opponent_adjust:
            faced = [base[o] for o in opps[team] if o in base]
            if faced:
                scored += league_ppg - sum(o[1] for o in faced) / len(faced)
                allowed += league_ppg - sum(o[0] for o in faced) / len(faced)
        out[team] = TeamRating(games=n, scored=scored, allowed=allowed)
    return out


def expected_score(
    sport: Sport,
    home: TeamRating | None,
    away: TeamRating | None,
) -> tuple[float, float]:
    league = sport.league_ppg
    h_off = home.scored if home else league
    h_def = home.allowed if home else league
    a_off = away.scored if away else league
    a_def = away.allowed if away else league
    if getattr(sport, "score_method", "additive") == "multiplicative" and league > 0:
        # log5-for-points: base scoring environment scaled by how good the
        # offense is and how leaky the opposing defense is, relative to league.
        # Captures matchup extremes the midpoint average compresses.
        h_exp = league * (h_off / league) * (a_def / league) + sport.hfa / 2
        a_exp = league * (a_off / league) * (h_def / league) - sport.hfa / 2
    else:
        h_exp = (h_off + a_def) / 2 + sport.hfa / 2
        a_exp = (a_off + h_def) / 2 - sport.hfa / 2
    return max(h_exp, league * 0.3), max(a_exp, league * 0.3)


@dataclass
class GenericGameProjection:
    home_exp: float
    away_exp: float
    home_win_prob: float
    total_mean: float
    margin_mean: float | None = None  # explicit home-margin mean; defaults to
    # home_exp - away_exp, but an EPA/Elo blend can override it so win and cover
    # probabilities stay consistent with each other.

    def _margin(self) -> float:
        return self.margin_mean if self.margin_mean is not None else self.home_exp - self.away_exp

    def prob_over(self, line: float, sport: Sport) -> float:
        if sport.model == "normal":
            return float(1 - stats.norm.cdf(line, self.total_mean, sport.sigma_total))
        lam_h, lam_a = self.home_exp, self.away_exp
        # total of two Poissons is Poisson(lam_h + lam_a)
        return float(1 - stats.poisson.cdf(int(line), lam_h + lam_a))

    def home_cover_prob(self, spread: float, sport: Sport) -> float:
        """P(home margin + spread > 0); spread is the home handicap
        (e.g. -1.5 for home favorite)."""
        margin_mean = self._margin()
        if sport.model == "normal":
            return float(1 - stats.norm.cdf(-spread, margin_mean, sport.sigma_margin))
        return _poisson_cover(self.home_exp, self.away_exp, spread)


def shift_win_prob(p: float, delta_pts: float, sigma: float) -> float:
    """Nudge a home win prob by a points adjustment (e.g. a rest-days edge):
    invert p -> implied margin mean through sigma, add delta, re-evaluate.
    No-op when delta is 0 or the sport has no margin sigma (Poisson models)."""
    if not delta_pts or sigma <= 0:
        return p
    p = min(max(p, 1e-6), 1 - 1e-6)
    mu = sigma * stats.norm.ppf(p)
    return float(stats.norm.cdf((mu + delta_pts) / sigma))


def project_game(
    sport: Sport,
    home: TeamRating | None,
    away: TeamRating | None,
) -> GenericGameProjection:
    h_exp, a_exp = expected_score(sport, home, away)
    margin_mean = None
    if sport.model == "normal":
        margin_mean = h_exp - a_exp
        win = float(1 - stats.norm.cdf(0, margin_mean, sport.sigma_margin))
    else:
        win = _poisson_win_prob(h_exp, a_exp)
    return GenericGameProjection(
        home_exp=round(h_exp, 2),
        away_exp=round(a_exp, 2),
        home_win_prob=round(win, 4),
        total_mean=round(h_exp + a_exp, 2),
        margin_mean=round(margin_mean, 2) if margin_mean is not None else None,
    )


def with_epa_margin(proj: GenericGameProjection, epa_margin: float,
                    sport: Sport, weight: float) -> GenericGameProjection:
    """Blend an EPA-derived home margin into a normal-model projection.

    ``weight`` is the EPA share (0 = unchanged points model, 1 = pure EPA).
    Recomputes home_win_prob and stores the blended margin so cover probability
    stays consistent. No-op for non-normal (Poisson) sports. This is the live
    integration point for the EPA ratings — gated on validation
    (scripts/validate_epa.py) before any sport sets epa_blend > 0."""
    if sport.model != "normal" or weight <= 0:
        return proj
    base = proj.margin_mean if proj.margin_mean is not None else proj.home_exp - proj.away_exp
    blended = (1 - weight) * base + weight * epa_margin
    win = float(1 - stats.norm.cdf(0, blended, sport.sigma_margin))
    return GenericGameProjection(
        home_exp=proj.home_exp, away_exp=proj.away_exp,
        home_win_prob=round(win, 4), total_mean=proj.total_mean,
        margin_mean=round(blended, 2),
    )


def _poisson_draws(lam_h: float, lam_a: float, n: int = 20_000, seed: int = 7):
    rng = np.random.default_rng(seed)
    h = rng.poisson(lam_h, n).astype(float)
    a = rng.poisson(lam_a, n).astype(float)
    ties = h == a
    while ties.any():  # overtime: keep adding small increments until broken
        h[ties] += rng.poisson(lam_h / 9.0, int(ties.sum()))
        a[ties] += rng.poisson(lam_a / 9.0, int(ties.sum()))
        ties = h == a
    return h, a


def _poisson_win_prob(lam_h: float, lam_a: float) -> float:
    h, a = _poisson_draws(lam_h, lam_a)
    return float((h > a).mean())


def _poisson_cover(lam_h: float, lam_a: float, spread: float) -> float:
    h, a = _poisson_draws(lam_h, lam_a)
    return float((h - a + spread > 0).mean())


# ---------------------------------------------------------------------------
# Generic props
# ---------------------------------------------------------------------------

# Box-score counting stats are heavily overdispersed and right-skewed
# (e.g. WNBA points var/mean ~6.5, rebounds ~2.9, assists ~2.4), so a
# Poisson (var = mean) or symmetric Normal mis-prices P(over): too
# confident in the tails and biased over for skewed stats. A negative
# binomial with a per-market "size" matches the shape — these dispersions
# were tuned against walk-forward calibration (reliability + bias) on
# 2023-2025 box logs. Higher size = closer to Poisson.
NB_DISPERSION = {
    # Football counts (checked first so multi-word "field goal" beats the
    # basketball "made", and so TD/scoring markets are matched). Short, collision
    # -prone keys (e.g. "int", which is a substring of "point") are avoided.
    # TD / scoring markets are near-Poisson at their low means (high size).
    "touchdown": 12.0, "td": 12.0,
    "reception": 8.0, "target": 8.0,
    "completion": 18.0, "attempt": 18.0,
    "interception": 8.0,
    "field goal": 8.0, "extra point": 10.0,
    "sack": 7.0, "tackle": 8.0,
    # Basketball
    "point": 5.0, "pts": 5.0, "pra": 6.0,
    "rebound": 7.0, "reb": 7.0,
    "assist": 9.0, "ast": 9.0,
    "three": 5.0, "3pm": 5.0, "made": 5.0,
    "steal": 6.0, "block": 6.0, "stl": 6.0, "blk": 6.0,
}
DEFAULT_NB_DISPERSION = 6.0


def prop_prob_over(projection: float, line: float, market_name: str) -> float:
    """P(stat > line) given a point projection and the market's name.

    Yardage / "longest" markets (continuous, fairly symmetric) use a Normal;
    all counting stats — basketball box stats and football counts (TDs,
    receptions, completions, field goals, …) — use a negative binomial whose
    dispersion is chosen by market keyword (see NB_DISPERSION). Anytime-TD /
    "1+" markets fall out naturally: a 0.5 line on a TD count is P(X >= 1).
    """
    name = (market_name or "").lower()
    if "yard" in name or "longest" in name:
        sd = 0.25 * projection + 10
        return float(1 - stats.norm.cdf(line, projection, sd))
    size = next((v for k, v in NB_DISPERSION.items() if k in name), DEFAULT_NB_DISPERSION)
    mean = max(projection, 1e-6)
    p = size / (size + mean)
    return float(1 - stats.nbinom.cdf(int(line), size, p))
