"""nflverse play-by-play loader → opponent-adjusted team EPA ratings.

OSP already ingests nflverse *game* results (scores + closing lines) via
``nfl_history.ingest_nflverse``. This adds the layer research identified as the
#1 NFL accuracy lever: the **play-by-play** feed, which carries per-play EPA,
success and win-probability, aggregated into opponent-adjusted team ratings
(``project547/epa.py``).

nflverse ships pre-built season parquet files on its data releases (no API key,
free, back to 1999; expected-pass metrics from 2006). We read those directly
rather than scraping:

    https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_<year>.parquet

The loader is injectable (`load=`) and also takes a local path, so it unit-tests
offline and so a season can be cached once and reused (the play-by-play files
are large; never re-download what's on disk). It does not touch the live
pipeline — the ratings it produces feed the staged EPA integration in the
roadmap.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from ..epa import GARBAGE_WP, TeamEPA, team_epa_ratings

log = logging.getLogger(__name__)

PBP_URL = ("https://github.com/nflverse/nflverse-data/releases/download/pbp/"
           "play_by_play_{year}.parquet")

# Real plays only: pass/run with a defined EPA. Excludes kneels, spikes,
# timeouts, no-plays — none of which carry team-strength signal.
VALID_PLAY_TYPES = ("pass", "run")


def _default_load(year: int):
    import pandas as pd
    return pd.read_parquet(PBP_URL.format(year=year))


def load_pbp(year: int, *, path: str | Path | None = None,
             load: Callable | None = None):
    """Return the season's play-by-play as a DataFrame.

    ``path`` reads a cached local parquet; ``load`` injects a transport (tests);
    otherwise the nflverse release is fetched. Prefer ``path`` in production to
    respect the 'never re-pull' rule once a season is on disk."""
    if load is not None:
        return load(year)
    if path is not None:
        import pandas as pd
        return pd.read_parquet(path)
    return _default_load(year)


def _to_plays(df, *, drop_garbage: bool = True) -> list[dict]:
    """Filter a pbp DataFrame to model-relevant plays and shape them for
    ``epa.team_epa_ratings``."""
    d = df[df["play_type"].isin(VALID_PLAY_TYPES)]
    d = d[d["epa"].notna() & d["posteam"].notna() & d["defteam"].notna()]
    if drop_garbage and "wp" in d.columns:
        # Drop plays where the game is effectively decided (either side).
        d = d[(d["wp"] > 1 - GARBAGE_WP) & (d["wp"] < GARBAGE_WP)]
    cols = ["posteam", "defteam", "epa"]
    if "success" in d.columns:
        cols.append("success")
    return d[cols].to_dict("records")


def team_ratings(year: int, *, path: str | Path | None = None,
                 load: Callable | None = None, lam: float = 25.0,
                 drop_garbage: bool = True) -> dict[str, TeamEPA]:
    """Opponent-adjusted EPA/success ratings for every NFL team in ``year``.

    Thin glue: load pbp → filter → ridge-adjust (see ``epa``). Walk-forward
    callers should pass a pbp frame already truncated to plays before the
    projection date."""
    df = load_pbp(year, path=path, load=load)
    plays = _to_plays(df, drop_garbage=drop_garbage)
    return team_epa_ratings(plays, league="nfl", lam=lam)
