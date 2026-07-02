"""Tennis match model — surface-aware player Elo → match-win probability.

Tennis is player-vs-player with no home side, so none of the team machinery
applies. We keep an Elo rating per player, updated match by match, plus a
per-surface Elo (hard / clay / grass) because form is strongly surface-dependent.
A player's effective rating blends their surface rating with their overall rating
(the overall carries the signal when the surface sample is thin), and the match
probability is the standard Elo logistic on the rating gap.

v1: match-winner only, no set/game handicaps and no bookmaker margin. Surface is
inferred from the tournament name (ESPN carries no surface field), so it's an
approximation — the blend falls back to overall when a surface is unknown or
thin. Ships behind the demonstrated-edge gate; a new market opens in PROBATION
and earns stake only as it proves CLV.
"""
from __future__ import annotations

from ..names import normalize

BASE = 1500.0
K = 24.0                 # update step; tennis has many matches/player/year
SURFACE_WEIGHT = 0.5     # weight on the surface rating vs the overall rating
SURFACES = ("hard", "clay", "grass")


class TennisElo:
    """Overall + per-surface player Elo. Feed it completed matches oldest-first."""

    def __init__(self, k: float = K, surface_weight: float = SURFACE_WEIGHT):
        self.k = k
        self.surface_weight = surface_weight
        self.overall: dict[str, float] = {}
        self.surface: dict[tuple[str, str], float] = {}
        self.matches: dict[str, int] = {}

    @staticmethod
    def _expected(ra: float, rb: float) -> float:
        return 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))

    def _overall(self, name: str) -> float:
        return self.overall.get(name, BASE)

    def _surface(self, name: str, surface: str) -> float:
        # fall back to the player's overall rating when the surface is unseen
        return self.surface.get((surface, name), self._overall(name))

    def update(self, winner: str, loser: str, surface: str | None = None) -> None:
        w, l = normalize(winner), normalize(loser)
        if not w or not l:
            return
        surface = surface if surface in SURFACES else None
        # overall
        ew = self._expected(self._overall(w), self._overall(l))
        self.overall[w] = self._overall(w) + self.k * (1.0 - ew)
        self.overall[l] = self._overall(l) - self.k * (1.0 - ew)
        # surface-specific
        if surface:
            sw, sl = self._surface(w, surface), self._surface(l, surface)
            es = self._expected(sw, sl)
            self.surface[(surface, w)] = sw + self.k * (1.0 - es)
            self.surface[(surface, l)] = sl - self.k * (1.0 - es)
        self.matches[w] = self.matches.get(w, 0) + 1
        self.matches[l] = self.matches.get(l, 0) + 1

    def rating(self, name: str, surface: str | None = None) -> float:
        """Effective rating: surface-blended when a valid surface is given."""
        n = normalize(name)
        overall = self._overall(n)
        if surface in SURFACES:
            surf = self._surface(n, surface)
            return self.surface_weight * surf + (1 - self.surface_weight) * overall
        return overall

    def match_prob(self, player1: str, player2: str,
                   surface: str | None = None) -> float:
        """P(player1 beats player2) on the given surface (Elo logistic)."""
        r1 = self.rating(player1, surface)
        r2 = self.rating(player2, surface)
        return round(self._expected(r1, r2), 4)

    def seen(self, name: str) -> int:
        return self.matches.get(normalize(name), 0)
