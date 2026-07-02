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
    "KC": ["Kansas City Royals", "Royals", "KC", "KCR", "KCA"],
    "LAD": ["Los Angeles Dodgers", "Dodgers", "LAD", "LAN"],
    "WSH": ["Washington Nationals", "Nationals", "WSH", "WSN", "WAS"],
    "NYM": ["New York Mets", "Mets", "NYM", "NYN"],
    "ATH": ["Athletics", "Oakland Athletics", "ATH", "OAK"],
    "PIT": ["Pittsburgh Pirates", "Pirates", "PIT"],
    "SD": ["San Diego Padres", "Padres", "SD", "SDP", "SDN"],
    "SEA": ["Seattle Mariners", "Mariners", "SEA"],
    "SF": ["San Francisco Giants", "Giants", "SF", "SFG", "SFN"],
    "STL": ["St. Louis Cardinals", "Cardinals", "STL", "SLN"],
    "TB": ["Tampa Bay Rays", "Rays", "TB", "TBR", "TBA"],
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

# canonical key = ESPN abbreviation (what our slates/box logs use); every other
# representation (full name, nickname, alt abbrevs) resolves to it. NHL box logs
# store full names while the slate uses abbrevs, so the full names are essential.
_NHL = {
    "ANA": ["Anaheim Ducks", "Ducks", "ANA"],
    "BOS": ["Boston Bruins", "Bruins", "BOS"],
    "BUF": ["Buffalo Sabres", "Sabres", "BUF"],
    "CGY": ["Calgary Flames", "Flames", "CGY"],
    "CAR": ["Carolina Hurricanes", "Hurricanes", "CAR"],
    "CHI": ["Chicago Blackhawks", "Blackhawks", "CHI"],
    "COL": ["Colorado Avalanche", "Avalanche", "COL"],
    "CBJ": ["Columbus Blue Jackets", "Blue Jackets", "CBJ"],
    "DAL": ["Dallas Stars", "Stars", "DAL"],
    "DET": ["Detroit Red Wings", "Red Wings", "DET"],
    "EDM": ["Edmonton Oilers", "Oilers", "EDM"],
    "FLA": ["Florida Panthers", "Panthers", "FLA"],
    "LA": ["Los Angeles Kings", "Kings", "LA", "LAK"],
    "MIN": ["Minnesota Wild", "Wild", "MIN"],
    "MTL": ["Montreal Canadiens", "Montréal Canadiens", "Canadiens", "MTL"],
    "NSH": ["Nashville Predators", "Predators", "NSH"],
    "NJ": ["New Jersey Devils", "Devils", "NJ", "NJD"],
    "NYI": ["New York Islanders", "Islanders", "NYI"],
    "NYR": ["New York Rangers", "Rangers", "NYR"],
    "OTT": ["Ottawa Senators", "Senators", "OTT"],
    "PHI": ["Philadelphia Flyers", "Flyers", "PHI"],
    "PIT": ["Pittsburgh Penguins", "Penguins", "PIT"],
    "SJ": ["San Jose Sharks", "Sharks", "SJ", "SJS"],
    "SEA": ["Seattle Kraken", "Kraken", "SEA"],
    "STL": ["St. Louis Blues", "Blues", "STL"],
    "TB": ["Tampa Bay Lightning", "Lightning", "TB", "TBL"],
    "TOR": ["Toronto Maple Leafs", "Maple Leafs", "TOR"],
    "UTAH": ["Utah Hockey Club", "Utah Mammoth", "Mammoth", "UTAH", "UTA"],
    "VAN": ["Vancouver Canucks", "Canucks", "VAN"],
    "VGK": ["Vegas Golden Knights", "Golden Knights", "VGK", "VEG"],
    "WSH": ["Washington Capitals", "Capitals", "WSH", "WAS"],
    "WPG": ["Winnipeg Jets", "Jets", "WPG", "WIN"],
    "ARI": ["Arizona Coyotes", "Coyotes", "ARI", "PHX"],
}

_NBA = {
    "ATL": ["Atlanta Hawks", "Hawks", "ATL"],
    "BOS": ["Boston Celtics", "Celtics", "BOS"],
    "BKN": ["Brooklyn Nets", "Nets", "BKN", "BRK"],
    "CHA": ["Charlotte Hornets", "Hornets", "CHA", "CHO"],
    "CHI": ["Chicago Bulls", "Bulls", "CHI"],
    "CLE": ["Cleveland Cavaliers", "Cavaliers", "CLE"],
    "DAL": ["Dallas Mavericks", "Mavericks", "DAL"],
    "DEN": ["Denver Nuggets", "Nuggets", "DEN"],
    "DET": ["Detroit Pistons", "Pistons", "DET"],
    "GS": ["Golden State Warriors", "Warriors", "GS", "GSW"],
    "HOU": ["Houston Rockets", "Rockets", "HOU"],
    "IND": ["Indiana Pacers", "Pacers", "IND"],
    "LAC": ["LA Clippers", "Los Angeles Clippers", "Clippers", "LAC"],
    "LAL": ["Los Angeles Lakers", "Lakers", "LAL"],
    "MEM": ["Memphis Grizzlies", "Grizzlies", "MEM"],
    "MIA": ["Miami Heat", "Heat", "MIA"],
    "MIL": ["Milwaukee Bucks", "Bucks", "MIL"],
    "MIN": ["Minnesota Timberwolves", "Timberwolves", "MIN"],
    "NO": ["New Orleans Pelicans", "Pelicans", "NO", "NOP"],
    "NY": ["New York Knicks", "Knicks", "NY", "NYK"],
    "OKC": ["Oklahoma City Thunder", "Thunder", "OKC"],
    "ORL": ["Orlando Magic", "Magic", "ORL"],
    "PHI": ["Philadelphia 76ers", "76ers", "Sixers", "PHI"],
    "PHX": ["Phoenix Suns", "Suns", "PHX", "PHO"],
    "POR": ["Portland Trail Blazers", "Trail Blazers", "POR"],
    "SAC": ["Sacramento Kings", "Kings", "SAC"],
    "SA": ["San Antonio Spurs", "Spurs", "SA", "SAS"],
    "TOR": ["Toronto Raptors", "Raptors", "TOR"],
    "UTAH": ["Utah Jazz", "Jazz", "UTAH", "UTA"],
    "WSH": ["Washington Wizards", "Wizards", "WSH", "WAS"],
}

_MAPS = {"MLB": _MLB, "WNBA": _WNBA, "NHL": _NHL, "NBA": _NBA}


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


def keys(sport: str) -> set[str]:
    """Canonical keys for a sport (its real teams) — used to filter out
    non-league entries like international sides in NHL box logs."""
    return set(_MAPS.get(sport, {}).keys())
