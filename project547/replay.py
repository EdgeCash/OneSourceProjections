"""Replay mode: rebuild the site's projections from the committed snapshot
library instead of live BettingPros/FantasyPros calls — zero API credits.

activate() monkeypatches the bettingpros/fantasypros client functions to
serve from data/history/. Free sources (MLB StatsAPI, ESPN) still go live;
they have no credit limits. Use when re-running the pipeline after a model
or UI tweak (scripts/rebuild_site.py).
"""

from __future__ import annotations

import gzip
import json
import logging

from . import config
from .clients import bettingpros, fantasypros

log = logging.getLogger(__name__)

SNAP_DIR = config.REPO_ROOT / "data" / "history" / "snapshots"
MARKETS_DIR = config.REPO_ROOT / "data" / "history" / "markets"
FP_DIR = config.REPO_ROOT / "data" / "history" / "fantasypros"


def _read_jsonl(stem):
    for path in (stem.with_suffix(".jsonl"), stem.with_suffix(".jsonl.gz")):
        if path.exists():
            opener = gzip.open if path.suffix == ".gz" else open
            with opener(path, "rt") as f:
                return [json.loads(x) for x in f if x.strip()]
    return []


def _latest_rows(sport: str, date: str) -> list[dict]:
    """Snapshot rows for a sport/date, keeping only the last capture."""
    rows = _read_jsonl(SNAP_DIR / sport.lower() / date)
    if not rows:
        return []
    last = max(r.get("captured_at", "") for r in rows)
    return [r for r in rows if r.get("captured_at", "") == last]


def activate():
    """Patch the paid-API clients to serve from the committed library."""

    def replay_markets(sport):
        path = MARKETS_DIR / f"{sport.lower()}.json"
        return json.loads(path.read_text()) if path.exists() else []

    def replay_events(sport, date):
        path = SNAP_DIR / sport.lower() / f"events_{date}.json"
        if path.exists():
            return json.loads(path.read_text())
        # fall back to bare event ids from the odds rows (team mapping then
        # comes from the offers' own participants)
        ids = {r.get("event_id") for r in _latest_rows(sport, date)
               if r.get("event_id") is not None}
        return [{"id": i} for i in ids]

    def replay_offers(sport, market_id, event_ids=None, location="ALL",
                      season=None):
        date = _CURRENT["date"]
        rows = [r for r in _latest_rows(sport, date)
                if r.get("kind") == "game"
                and int(r.get("market_id") or -1) == int(market_id)]
        return rows  # pre-flattened; flatten_offers passes through

    def replay_props(sport, date, market_ids=None, location="ALL"):
        return [r for r in _latest_rows(sport, date) if r.get("kind") == "prop"]

    def replay_fp_mlb(season, proj_type="daily", date=None, position=None):
        path = FP_DIR / f"mlb_{date}.json"
        return json.loads(path.read_text()) if path.exists() else []

    bettingpros.markets = replay_markets  # market_lookup builds on this
    bettingpros.events = replay_events
    bettingpros.offers = replay_offers
    bettingpros.props = replay_props
    fantasypros.mlb_projections = replay_fp_mlb
    log.info("replay mode active: BettingPros/FantasyPros served from library")


_CURRENT = {"date": None}


def set_date(date: str):
    """Replay offers() has no date argument (mirrors the live signature),
    so the rebuild loop pins the date here before each slate."""
    _CURRENT["date"] = date
