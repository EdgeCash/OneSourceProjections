"""Hourly odds snapshots. Each run appends the current BettingPros game and
prop lines (with a UTC capture timestamp) to an append-only per-day log, so
the time series accumulates and the last pre-game snapshot per event becomes
that game's closing line. This is the same mechanism that produced the
imported open/close history; running it forward builds our own, from the
exact source the model uses, enabling true CLV going forward.

Files: data/history/snapshots/<sport>/<date>.jsonl
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from . import config
from .clients import bettingpros
from .sports import SPORTS, active_sports

log = logging.getLogger(__name__)

SNAP_DIR = config.REPO_ROOT / "data" / "history" / "snapshots"


def _append(sport: str, date: str, rows: list[dict]):
    path = SNAP_DIR / sport.lower() / f"{date}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        for r in rows:
            f.write(json.dumps(r, default=str) + "\n")


def _save_raw_sample(sport: str, date: str, kind: str, payload: list):
    """Keep the first couple of raw objects per sport/kind/date so the real
    payload schema is committed and inspectable offline (the parsers were
    initially written against guessed shapes)."""
    if not payload:
        return
    path = SNAP_DIR / "raw" / f"{sport.lower()}_{kind}_{date}.json"
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload[:2], indent=1, default=str))


def snapshot(date: str, sports: list[str] | None = None) -> dict:
    """Capture and append current game + prop odds for a date. Returns a
    per-sport count of rows written. Degrades gracefully per sport."""
    sports = sports or active_sports(date)
    captured = datetime.now(timezone.utc).isoformat()
    counts: dict[str, int] = {}

    for sk in sports:
        if sk not in SPORTS:
            continue
        rows: list[dict] = []
        try:
            _save_raw_sample(sk, date, "markets", bettingpros.markets(sk))
        except Exception as e:
            log.warning("%s snapshot: markets unavailable: %s", sk, e)
        try:
            events = bettingpros.events(sk, date)
            event_ids = [e.get("id") for e in events if e.get("id")]
            _save_raw_sample(sk, date, "events", events)
        except Exception as e:
            log.warning("%s snapshot: events unavailable: %s", sk, e)
            counts[sk] = 0
            continue

        # game markets (moneyline / total / spread)
        try:
            for market, mid in bettingpros.game_market_ids(sk).items():
                if mid is None:
                    continue
                raw = bettingpros.offers(sk, mid, event_ids,
                                         season=int(date[:4]))
                if market == "moneyline":
                    _save_raw_sample(sk, date, "offers", raw)
                for r in bettingpros.flatten_offers(raw):
                    r.update({"captured_at": captured, "sport": sk, "date": date,
                              "kind": "game", "market": market})
                    rows.append(r)
        except Exception as e:
            log.warning("%s snapshot: game offers unavailable: %s", sk, e)

        # player props (BettingPros consensus + premium projection/EV)
        try:
            raw_props = bettingpros.props(sk, date)
            _save_raw_sample(sk, date, "props", raw_props)
            for r in bettingpros.flatten_props(raw_props):
                r.update({"captured_at": captured, "sport": sk, "date": date,
                          "kind": "prop"})
                rows.append(r)
        except Exception as e:
            log.warning("%s snapshot: props unavailable: %s", sk, e)

        if rows:
            _append(sk, date, rows)
        counts[sk] = len(rows)
        log.info("%s snapshot: %d rows for %s", sk, len(rows), date)
    return counts
