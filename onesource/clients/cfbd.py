"""CollegeFootballData (CFBD) API client — the canonical free NCAAF source.

Research (docs/research/02-data-and-features.md) flagged CFBD as the richest
free college-football dataset and a hard requirement for an accurate NCAAF
model: it supplies the exact signals the NFL gets from nflverse plus the
college-specific ones that connect an otherwise disjoint schedule —

  * team PPA (CFBD's EPA-per-play equivalent), offense & defense
  * SP+ / FPI / Elo power ratings (ready-made priors)
  * returning production and recruiting/transfer-portal talent (the priors that
    let a Bayesian model project teams that share no common opponents)
  * betting lines (open/close) and game results

Auth is a Bearer key (free tier ~1,000 calls/month; a $10/mo Patreon tier
raises it to 75k — see the roadmap before season backfills). The key is read
through ``config.secret`` like every other credential; never hard-coded.

The free-tier call budget is small, so callers should cache aggressively
(the snapshot store already does this for live data). This client is
transport-injectable (`fetch=`) so it unit-tests fully offline.
"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from typing import Callable

from .. import config

log = logging.getLogger(__name__)

BASE = "https://api.collegefootballdata.com"

# Read the key lazily so importing this module never requires a key.
CFBD_API_KEY = lambda: config.secret("CFBD_API_KEY")  # noqa: E731

Fetch = Callable[[str, dict], list | dict]


def _default_fetch(url: str, headers: dict) -> list | dict:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (trusted host)
        return json.loads(resp.read().decode())


class CFBDClient:
    """Thin typed wrapper over the CFBD REST endpoints we use.

    Pass ``fetch`` to inject a transport in tests; in production it defaults to
    an authenticated urllib call. Raises RuntimeError if no key is configured
    and the default transport is used."""

    def __init__(self, api_key: str | None = None, fetch: Fetch | None = None):
        self._key = api_key if api_key is not None else CFBD_API_KEY()
        self._fetch = fetch or self._authed_fetch

    def _authed_fetch(self, url: str, params: dict) -> list | dict:
        if not self._key:
            raise RuntimeError(
                "CFBD_API_KEY not set. Add it to .env (free key at "
                "https://collegefootballdata.com/key)."
            )
        q = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        full = f"{url}?{q}" if q else url
        return _default_fetch(full, {"Authorization": f"Bearer {self._key}",
                                     "Accept": "application/json"})

    def _get(self, path: str, **params) -> list | dict:
        return self._fetch(f"{BASE}{path}", params)

    # --- power-rating priors -------------------------------------------------
    def sp_ratings(self, year: int, team: str | None = None) -> list[dict]:
        """SP+ ratings (overall/offense/defense/special teams). The single best
        ready-made NCAAF prior; use to seed preseason and shrink sparse teams."""
        return self._get("/ratings/sp", year=year, team=team)  # type: ignore[return-value]

    def fpi_ratings(self, year: int, team: str | None = None) -> list[dict]:
        return self._get("/ratings/fpi", year=year, team=team)  # type: ignore[return-value]

    # --- EPA / PPA -----------------------------------------------------------
    def team_ppa(self, year: int, week: int | None = None,
                 season_type: str = "regular") -> list[dict]:
        """Team Predicted Points Added (CFBD's opponent-adjusted EPA/play),
        offense & defense. Directly analogous to nflverse team EPA."""
        return self._get("/ppa/teams", year=year, week=week,  # type: ignore[return-value]
                         seasonType=season_type)

    def plays(self, year: int, week: int, season_type: str = "regular") -> list[dict]:
        """Play-by-play (carries per-play ppa). Heavy on the call budget —
        prefer team_ppa unless you need raw plays for custom adjustment."""
        return self._get("/plays", year=year, week=week,  # type: ignore[return-value]
                         seasonType=season_type)

    # --- college-specific priors --------------------------------------------
    def returning_production(self, year: int, team: str | None = None) -> list[dict]:
        """Returning production (overall/offense/defense). The strongest
        preseason signal for college (SP+ leans on it heavily)."""
        return self._get("/player/returning", year=year, team=team)  # type: ignore[return-value]

    def talent(self, year: int) -> list[dict]:
        """Composite team talent (recruiting + portal). Connects disjoint
        schedules so far-apart teams can be compared."""
        return self._get("/talent", year=year)  # type: ignore[return-value]

    # --- games & markets -----------------------------------------------------
    def games(self, year: int, week: int | None = None,
              season_type: str = "regular") -> list[dict]:
        return self._get("/games", year=year, week=week,  # type: ignore[return-value]
                         seasonType=season_type)

    def lines(self, year: int, week: int | None = None) -> list[dict]:
        """Betting lines (open/close spread & total) per game and book."""
        return self._get("/lines", year=year, week=week)  # type: ignore[return-value]
