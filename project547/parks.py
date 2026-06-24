"""MLB park factor lookup. Resolves a team given as a full name, city,
nickname, or any common abbreviation variant to its home-park run factor
(league mean = 1.0). Returns 1.0 (neutral) for anything unknown."""

from __future__ import annotations

import json
from functools import lru_cache

from .config import REPO_ROOT
from .names import normalize

_FACTORS_PATH = REPO_ROOT / "data" / "history" / "park_factors.json"

# Canonical abbreviation (as used in park_factors.json) -> alternates seen
# across sources (StatsAPI full names, BettingPros, backfill). We index by
# normalized full name, nickname, city, and every abbreviation.
_TEAMS = {
    "ARI": ("Arizona Diamondbacks", "diamondbacks", "arizona", ["AZ", "ARI"]),
    "ATL": ("Atlanta Braves", "braves", "atlanta", ["ATL"]),
    "BAL": ("Baltimore Orioles", "orioles", "baltimore", ["BAL"]),
    "BOS": ("Boston Red Sox", "red sox", "boston", ["BOS"]),
    "CHC": ("Chicago Cubs", "cubs", "chicago cubs", ["CHC", "CHN"]),
    "CWS": ("Chicago White Sox", "white sox", "chicago white sox", ["CWS", "CHW", "CHA"]),
    "CIN": ("Cincinnati Reds", "reds", "cincinnati", ["CIN"]),
    "CLE": ("Cleveland Guardians", "guardians", "cleveland", ["CLE"]),
    "COL": ("Colorado Rockies", "rockies", "colorado", ["COL"]),
    "DET": ("Detroit Tigers", "tigers", "detroit", ["DET"]),
    "HOU": ("Houston Astros", "astros", "houston", ["HOU"]),
    "KC": ("Kansas City Royals", "royals", "kansas city", ["KC", "KCR"]),
    "LAA": ("Los Angeles Angels", "angels", "los angeles angels", ["LAA", "ANA"]),
    "LAD": ("Los Angeles Dodgers", "dodgers", "los angeles dodgers", ["LAD", "LAN"]),
    "MIA": ("Miami Marlins", "marlins", "miami", ["MIA", "FLA"]),
    "MIL": ("Milwaukee Brewers", "brewers", "milwaukee", ["MIL"]),
    "MIN": ("Minnesota Twins", "twins", "minnesota", ["MIN"]),
    "NYM": ("New York Mets", "mets", "new york mets", ["NYM", "NYN"]),
    "NYY": ("New York Yankees", "yankees", "new york yankees", ["NYY", "NYA"]),
    "ATH": ("Athletics", "athletics", "oakland", ["ATH", "OAK"]),
    "PHI": ("Philadelphia Phillies", "phillies", "philadelphia", ["PHI"]),
    "PIT": ("Pittsburgh Pirates", "pirates", "pittsburgh", ["PIT"]),
    "SD": ("San Diego Padres", "padres", "san diego", ["SD", "SDP"]),
    "SF": ("San Francisco Giants", "giants", "san francisco", ["SF", "SFG"]),
    "SEA": ("Seattle Mariners", "mariners", "seattle", ["SEA"]),
    "STL": ("St. Louis Cardinals", "cardinals", "st louis", ["STL", "SLN"]),
    "TB": ("Tampa Bay Rays", "rays", "tampa bay", ["TB", "TBR"]),
    "TEX": ("Texas Rangers", "rangers", "texas", ["TEX"]),
    "TOR": ("Toronto Blue Jays", "blue jays", "toronto", ["TOR"]),
    "WSH": ("Washington Nationals", "nationals", "washington", ["WSH", "WSN", "WAS"]),
}


@lru_cache(maxsize=1)
def _factors() -> dict[str, float]:
    if not _FACTORS_PATH.exists():
        return {}
    return json.loads(_FACTORS_PATH.read_text())["factors"]


@lru_cache(maxsize=1)
def _index() -> dict[str, list[str]]:
    """normalized key -> list of abbreviation candidates to try against the
    park-factor file (whose keys may use any one of the variants)."""
    idx = {}
    for abbr, (full, nick, city, alts) in _TEAMS.items():
        candidates = [abbr, *alts]
        for key in (full, nick, city, abbr, *alts):
            idx[normalize(key)] = candidates
    return idx


def factor(team: str) -> float:
    """Park run factor for a team's home venue (league mean 1.0). Tolerant
    of full names, nicknames, cities, and abbreviation variants."""
    if not team:
        return 1.0
    f = _factors()
    for cand in _index().get(normalize(team), [str(team).upper()]):
        if cand in f:
            return f[cand]
    return 1.0
