"""Player/team name normalization so rows from BettingPros, FantasyPros,
MLB StatsAPI, and FanGraphs can be joined on names."""

import re
import unicodedata

_SUFFIXES = re.compile(r"\b(jr|sr|ii|iii|iv)\.?$", re.IGNORECASE)


def normalize(name: str) -> str:
    if not name:
        return ""
    # strip accents (José -> Jose)
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = name.lower().replace(".", "").replace("'", "").strip()
    name = _SUFFIXES.sub("", name).strip()
    return re.sub(r"\s+", " ", name)
