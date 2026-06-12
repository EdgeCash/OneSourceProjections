"""Canonical team identity: resolve a team given as a full name, city, or
any abbreviation variant to one stable key, so data from different sources
(StatsAPI full names, ESPN, abbreviated backfill) joins cleanly.

canon(sport, name) -> canonical key (str), or normalize(name) if unknown.
"""

from __future__ import annotations

from functools import lru_cache

from .names import normalize

# canonical key -> every representation we might see
_MLB = {
    "LAA": ["Los Angeles Angels", "Angels", "LAA", "ANA"],
    "ARI": ["Arizona Diamondbacks", "Diamondbacks", "ARI", "AZ"],
    "BAL": ["Baltimore Orioles", "Orioles", "BAL"],
    "BOS": ["Boston Red Sox", "Red Sox", "BOS"],
    "CHC": ["Chicago Cubs", "Cubs", "CHC", "CHN"],
    "CIN": ["Cincinnati Reds", "Reds", "CIN"],
    "CLE": ["Cleveland Guardians", "Guardians", "CLE"],
    "COL": ["Colorado Rockies", "Rockies", "COL"],
    "DET": ["Detroit Tigers", "Tigers", "DET"],
    "HOU": ["Houston Astros", "Astros", "HOU"],
    "KC": ["Kansas City Royals", "Royals", "KC", "KCR"],
    "LAD": ["Los Angeles Dodgers", "Dodgers", "LAD", "LAN"],
    "WSH": ["Washington Nationals", "Nationals", "WSH", "WSN", "WAS"],
    "NYM": ["New York Mets", "Mets", "NYM", "NYN"],
    "ATH": ["Athletics", "Oakland Athletics", "ATH", "OAK"],
    "PIT": ["Pittsburgh Pirates", "Pirates", "PIT"],
    "SD": ["San Diego Padres", "Padres", "SD", "SDP"],
    "SEA": ["Seattle Mariners", "Mariners", "SEA"],
    "SF": ["San Francisco Giants", "Giants", "SF", "SFG"],
    "STL": ["St. Louis Cardinals", "Cardinals", "STL", "SLN"],
    "TB": ["Tampa Bay Rays", "Rays", "TB", "TBR"],
    "TEX": ["Texas Rangers", "Rangers", "TEX"],
    "TOR": ["Toronto Blue Jays", "Blue Jays", "TOR"],
    "MIN": ["Minnesota Twins", "Twins", "MIN"],
    "PHI": ["Philadelphia Phillies", "Phillies", "PHI"],
    "ATL": ["Atlanta Braves", "Braves", "ATL"],
    "CWS": ["Chicago White Sox", "White Sox", "CWS", "CHW", "CHA"],
    "MIA": ["Miami Marlins", "Marlins", "MIA", "FLA"],
    "NYY": ["New York Yankees", "Yankees", "NYY", "NYA"],
    "MIL": ["Milwaukee Brewers", "Brewers", "MIL"],
}

_WNBA = {
    "ATL": ["Atlanta Dream", "Dream", "ATL"],
    "CHI": ["Chicago Sky", "Sky", "CHI"],
    "CON": ["Connecticut Sun", "Sun", "CON", "CONN"],
    "DAL": ["Dallas Wings", "Wings", "DAL"],
    "GS": ["Golden State Valkyries", "Valkyries", "GS", "GSV"],
    "IND": ["Indiana Fever", "Fever", "IND"],
    "LV": ["Las Vegas Aces", "Aces", "LV", "LVA"],
    "LA": ["Los Angeles Sparks", "Sparks", "LA", "LAS"],
    "MIN": ["Minnesota Lynx", "Lynx", "MIN"],
    "NY": ["New York Liberty", "Liberty", "NY", "NYL"],
    "PHX": ["Phoenix Mercury", "Mercury", "PHX", "PHO"],
    "POR": ["Portland Fire", "Fire", "POR"],
    "SEA": ["Seattle Storm", "Storm", "SEA"],
    "TOR": ["Toronto Tempo", "Tempo", "TOR"],
    "WSH": ["Washington Mystics", "Mystics", "WSH", "WAS"],
}

_MAPS = {"MLB": _MLB, "WNBA": _WNBA}


@lru_cache(maxsize=8)
def _index(sport: str) -> dict[str, str]:
    idx = {}
    for key, reps in _MAPS.get(sport, {}).items():
        for r in reps:
            idx[normalize(r)] = key
    return idx


def canon(sport: str, name: str) -> str:
    if not name:
        return ""
    return _index(sport).get(normalize(name), normalize(name))
