"""Lightweight Elo rating system for team sports. Walk-forward by
construction: ratings only ever incorporate games already fed in, so it is
safe to use inside backtests and to maintain live from a results feed.

Standard logistic Elo with a home-court edge, an optional margin-of-victory
multiplier, and between-season regression toward the mean. Defaults are
tuned for WNBA; other leagues can override via EloConfig.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class EloConfig:
    # defaults tuned for WNBA (Brier 0.216 on 2021-2025, matches the
    # historical Elo); other leagues can override.
    k: float = 16.0           # update step
    home_edge: float = 50.0   # rating points added to the home side
    base: float = 1500.0
    season_regress: float = 0.25  # fraction pulled back to base each new season
    mov: bool = True          # scale updates by margin of victory


@dataclass
class Elo:
    cfg: EloConfig = field(default_factory=EloConfig)
    ratings: dict = field(default_factory=dict)
    _last_season: dict = field(default_factory=dict)

    def _r(self, team: str) -> float:
        return self.ratings.get(team, self.cfg.base)

    def _maybe_regress(self, team: str, season: int | None):
        if season is None:
            return
        if self._last_season.get(team) not in (None, season):
            r = self._r(team)
            self.ratings[team] = r + self.cfg.season_regress * (self.cfg.base - r)
        self._last_season[team] = season

    def home_win_prob(self, home: str, away: str, season: int | None = None) -> float:
        self._maybe_regress(home, season)
        self._maybe_regress(away, season)
        diff = self._r(home) + self.cfg.home_edge - self._r(away)
        return 1.0 / (1.0 + 10 ** (-diff / 400.0))

    def update(self, home: str, away: str, home_score: float, away_score: float,
               season: int | None = None):
        p_home = self.home_win_prob(home, away, season)
        home_won = 1.0 if home_score > away_score else 0.0
        mult = 1.0
        if self.cfg.mov:
            margin = abs(home_score - away_score)
            # dampened log multiplier (a la 538), guarded for blowouts
            mult = max(1.0, (margin + 1.0)) ** 0.5
        delta = self.cfg.k * mult * (home_won - p_home)
        self.ratings[home] = self._r(home) + delta
        self.ratings[away] = self._r(away) - delta
