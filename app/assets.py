"""Team logos and player headshots, with a graceful monogram fallback so the
UI looks finished even when a CDN URL is missing or 404s.

Logo sources (free):
  - MLB:  https://www.mlbstatic.com/team-logos/<mlbam_id>.svg
  - MLB headshots: midfield.mlbstatic.com by MLBAM person id
  - WNBA/NBA/NHL: ESPN logo CDN by team abbreviation

We resolve a team given as a full name, city, or abbreviation. Anything we
can't resolve renders as initials on a deterministic color (see monogram).
"""

from __future__ import annotations

import hashlib

from onesource.names import normalize

# MLBAM team ids -> (full name, [abbrev variants])
_MLB = {
    108: ("Los Angeles Angels", ["LAA", "ANA"]),
    109: ("Arizona Diamondbacks", ["ARI", "AZ"]),
    110: ("Baltimore Orioles", ["BAL"]),
    111: ("Boston Red Sox", ["BOS"]),
    112: ("Chicago Cubs", ["CHC", "CHN"]),
    113: ("Cincinnati Reds", ["CIN"]),
    114: ("Cleveland Guardians", ["CLE"]),
    115: ("Colorado Rockies", ["COL"]),
    116: ("Detroit Tigers", ["DET"]),
    117: ("Houston Astros", ["HOU"]),
    118: ("Kansas City Royals", ["KC", "KCR"]),
    119: ("Los Angeles Dodgers", ["LAD", "LAN"]),
    120: ("Washington Nationals", ["WSH", "WSN", "WAS"]),
    121: ("New York Mets", ["NYM", "NYN"]),
    133: ("Athletics", ["ATH", "OAK"]),
    134: ("Pittsburgh Pirates", ["PIT"]),
    135: ("San Diego Padres", ["SD", "SDP"]),
    136: ("Seattle Mariners", ["SEA"]),
    137: ("San Francisco Giants", ["SF", "SFG"]),
    138: ("St. Louis Cardinals", ["STL", "SLN"]),
    139: ("Tampa Bay Rays", ["TB", "TBR"]),
    140: ("Texas Rangers", ["TEX"]),
    141: ("Toronto Blue Jays", ["TOR"]),
    142: ("Minnesota Twins", ["MIN"]),
    143: ("Philadelphia Phillies", ["PHI"]),
    144: ("Atlanta Braves", ["ATL"]),
    145: ("Chicago White Sox", ["CWS", "CHW", "CHA"]),
    146: ("Miami Marlins", ["MIA", "FLA"]),
    147: ("New York Yankees", ["NYY", "NYA"]),
    158: ("Milwaukee Brewers", ["MIL"]),
}

# ESPN logo abbreviations by league
_ESPN = {
    "WNBA": {
        "Atlanta Dream": "atl", "Chicago Sky": "chi", "Connecticut Sun": "conn",
        "Dallas Wings": "dal", "Golden State Valkyries": "gsv",
        "Indiana Fever": "ind", "Las Vegas Aces": "lv",
        "Los Angeles Sparks": "la", "Minnesota Lynx": "min",
        "New York Liberty": "ny", "Phoenix Mercury": "phx",
        "Portland Fire": "por", "Seattle Storm": "sea",
        "Toronto Tempo": "tor", "Washington Mystics": "wsh",
        # abbreviations our WNBA data uses
        "ATL": "atl", "CHI": "chi", "CON": "conn", "DAL": "dal", "GS": "gsv",
        "IND": "ind", "LV": "lv", "LA": "la", "MIN": "min", "NY": "ny",
        "PHX": "phx", "POR": "por", "SEA": "sea", "TOR": "tor", "WSH": "wsh",
    },
}

_MLB_INDEX: dict[str, int] = {}
for _id, (_full, _alts) in _MLB.items():
    _MLB_INDEX[normalize(_full)] = _id
    for _a in _alts:
        _MLB_INDEX[normalize(_a)] = _id

_ESPN_INDEX: dict[str, dict[str, str]] = {
    lg: {normalize(k): v for k, v in m.items()} for lg, m in _ESPN.items()
}

_MONOGRAM_COLORS = [
    "#1f6feb", "#238636", "#a371f7", "#db61a2", "#e3651d", "#1a7f7f",
    "#9e6a03", "#bc4c00", "#0969da", "#6e7781", "#cf222e", "#8250df",
]


def team_logo_url(sport: str, team: str) -> str | None:
    """Best-effort logo URL, or None if we can't resolve the team."""
    if not team:
        return None
    key = normalize(team)
    if sport == "MLB":
        tid = _MLB_INDEX.get(key)
        return f"https://www.mlbstatic.com/team-logos/{tid}.svg" if tid else None
    abbr = _ESPN_INDEX.get(sport, {}).get(key)
    if abbr:
        return f"https://a.espncdn.com/i/teamlogos/{sport.lower()}/500/{abbr}.png"
    return None


def mlb_headshot_url(player_id: int | str | None) -> str | None:
    if not player_id:
        return None
    return f"https://midfield.mlbstatic.com/v1/people/{player_id}/spots/120"


def monogram(name: str) -> tuple[str, str]:
    """(initials, hex color) for a fallback badge — deterministic by name."""
    if not name:
        return "?", _MONOGRAM_COLORS[0]
    parts = [p for p in str(name).replace(".", " ").split() if p]
    if len(parts) >= 2:
        initials = (parts[0][0] + parts[-1][0]).upper()
    else:
        initials = (parts[0][:2] if parts else "?").upper()
    h = int(hashlib.sha256(str(name).encode()).hexdigest(), 16)
    return initials, _MONOGRAM_COLORS[h % len(_MONOGRAM_COLORS)]


def team_badge_html(sport: str, team: str, size: int = 44) -> str:
    """An <img> that falls back to a colored monogram if the logo fails."""
    initials, color = monogram(team)
    fallback = (
        f"<div style=\"width:{size}px;height:{size}px;border-radius:50%;"
        f"background:{color};color:#fff;display:flex;align-items:center;"
        f"justify-content:center;font-weight:700;font-size:{int(size * 0.36)}px;"
        f"\">{initials}</div>"
    )
    url = team_logo_url(sport, team)
    if not url:
        return fallback
    esc = fallback.replace('"', "&quot;")
    return (
        f'<img src="{url}" width="{size}" height="{size}" '
        f'style="object-fit:contain;" '
        f"onerror=\"this.outerHTML='{esc}'\" alt=\"{team}\">"
    )
