# nflverse play-by-play (EPA source)

This directory holds nflverse season play-by-play parquet — the source for the
opponent-adjusted EPA ratings (`project547/epa.py`, `project547/clients/nflverse.py`).

**Not committed here:** the files are free public data (no key) and the
sandbox these were prepared in throttles large downloads. Pull them where the
network is reliable (your machine or CI):

```python
from project547.clients import nflverse
# downloads + caches play_by_play_<year>.parquet, returns team EPA ratings
ratings = nflverse.team_ratings(2024, path="data/history/pbp/nfl/play_by_play_2024.parquet")
```

or directly:
`https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_<year>.parquet`

Then run the Stage-1 validation gate:
```
python scripts/validate_epa.py
```
EPA only gets wired into live projections (`Sport.epa_blend > 0`) if it beats
the points model walk-forward there. See docs/ACCURACY_ROADMAP.md.
