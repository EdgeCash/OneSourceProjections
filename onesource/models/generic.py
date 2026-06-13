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


def team_ratings(results: list[dict], league_ppg: float) -> dict[str, TeamRating]:
    """results: [{home_team, away_team, home_score, away_score}, ...]"""
    raw: dict[str, list[tuple[float, float]]] = {}
    for g in results:
        raw.setdefault(g["home_team"], []).append((g["home_score"], g["away_score"]))
        raw.setdefault(g["away_team"], []).append((g["away_score"], g["home_score"]))
    out = {}
    for team, games in raw.items():
        n = len(games)
        scored = sum(s for s, _ in games) / n
        allowed = sum(a for _, a in games) / n
        # shrink harder when the sample is thin
        w = RATING_SHRINK * min(1.0, n / 10)
        out[team] = TeamRating(
            games=n,
            scored=w * scored + (1 - w) * league_ppg,
            allowed=w * allowed + (1 - w) * league_ppg,
        )
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

    def prob_over(self, line: float, sport: Sport) -> float:
        if sport.model == "normal":
            return float(1 - stats.norm.cdf(line, self.total_mean, sport.sigma_total))
        lam_h, lam_a = self.home_exp, self.away_exp
        # total of two Poissons is Poisson(lam_h + lam_a)
        return float(1 - stats.poisson.cdf(int(line), lam_h + lam_a))

    def home_cover_prob(self, spread: float, sport: Sport) -> float:
        """P(home margin + spread > 0); spread is the home handicap
        (e.g. -1.5 for home favorite)."""
        margin_mean = self.home_exp - self.away_exp
        if sport.model == "normal":
            return float(1 - stats.norm.cdf(-spread, margin_mean, sport.sigma_margin))
        return _poisson_cover(self.home_exp, self.away_exp, spread)


def project_game(
    sport: Sport,
    home: TeamRating | None,
    away: TeamRating | None,
) -> GenericGameProjection:
    h_exp, a_exp = expected_score(sport, home, away)
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
    "point": 5.0, "pts": 5.0, "pra": 6.0,
    "rebound": 7.0, "reb": 7.0,
    "assist": 9.0, "ast": 9.0,
    "three": 5.0, "3pm": 5.0, "made": 5.0,
    "steal": 6.0, "block": 6.0, "stl": 6.0, "blk": 6.0,
}
DEFAULT_NB_DISPERSION = 6.0


def prop_prob_over(projection: float, line: float, market_name: str) -> float:
    """P(stat > line) given a point projection and the market's name.

    Yardage markets (continuous, fairly symmetric) use a Normal; all
    counting stats use a negative binomial whose dispersion is chosen by
    market keyword (see NB_DISPERSION) to capture overdispersion and skew.
    """
    name = (market_name or "").lower()
    if "yard" in name:
        sd = 0.25 * projection + 10
        return float(1 - stats.norm.cdf(line, projection, sd))
    size = next((v for k, v in NB_DISPERSION.items() if k in name), DEFAULT_NB_DISPERSION)
    mean = max(projection, 1e-6)
    p = size / (size + mean)
    return float(1 - stats.nbinom.cdf(int(line), size, p))
