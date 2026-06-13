"""Closing Line Value (CLV) — the lowest-variance proxy for betting skill.

The hourly snapshot store (data/history/snapshots/<sport>/<date>.jsonl) keeps
a timestamped series of BettingPros odds; the latest capture before a game is
that game's closing line. We de-vig it to a fair probability and compare it to
the price each recommended bet was made at: if the model was getting a better
price than the no-vig close, that's positive CLV — the strongest early signal
that an edge is real, long before win/loss ROI converges.
"""

from __future__ import annotations

import gzip
import json
from collections import defaultdict

from . import config, odds
from .names import normalize

SNAP_DIR = config.REPO_ROOT / "data" / "history" / "snapshots"


def _load_rows(sport: str, date: str, snap_dir=None) -> list[dict]:
    base = (snap_dir or SNAP_DIR) / sport.lower()
    rows: list[dict] = []
    for name in (f"{date}.jsonl", f"{date}.jsonl.gz"):
        path = base / name
        if not path.exists():
            continue
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt") as f:
            rows += [json.loads(ln) for ln in f if ln.strip()]
    return rows


def _best(rows: list[dict]) -> float | None:
    odds_ = [r["odds"] for r in rows if r.get("odds") is not None]
    return max(odds_) if odds_ else None


def closing_lines(sport: str, date: str, snap_dir=None) -> dict:
    """Per-game de-vigged closing probabilities from the snapshot store.

    Returns {frozenset({norm_home, norm_away}): {"moneyline": {norm_team:
    fair_prob}, "total": {"line": x, "over": p, "under": 1-p}}}. Uses the
    latest capture in the day's log as the close and best price per side.
    """
    rows = [r for r in _load_rows(sport, date, snap_dir) if r.get("kind") == "game"]
    if not rows:
        return {}
    last = max((r.get("captured_at") or "") for r in rows)
    rows = [r for r in rows if (r.get("captured_at") or "") == last]

    events: dict = defaultdict(list)
    for r in rows:
        events[r.get("event_id")].append(r)

    out: dict = {}
    for ers in events.values():
        teams = {normalize(r["participant"]) for r in ers
                 if r.get("market") == "moneyline" and r.get("participant")}
        rec: dict = {}

        # moneyline: best price per team -> de-vig two-way
        by_team: dict = defaultdict(list)
        for r in ers:
            if r.get("market") == "moneyline" and r.get("participant"):
                by_team[normalize(r["participant"])].append(r)
        prices = {t: _best(rs) for t, rs in by_team.items()}
        prices = {t: p for t, p in prices.items() if p is not None}
        if len(prices) == 2:
            (ta, oa), (tb, ob) = prices.items()
            fair = odds.fair_two_way(oa, ob)
            if fair:
                rec["moneyline"] = {ta: fair[0], tb: fair[1]}

        # total: best over / best under -> de-vig
        overs = [r for r in ers if r.get("market") == "total"
                 and "over" in str(r.get("selection", "")).lower()]
        unders = [r for r in ers if r.get("market") == "total"
                  and "under" in str(r.get("selection", "")).lower()]
        bo, bu = _best(overs), _best(unders)
        if bo is not None and bu is not None:
            fair = odds.fair_two_way(bo, bu)
            if fair:
                lines = sorted(r["line"] for r in overs if r.get("line") is not None)
                rec["total"] = {"line": lines[len(lines) // 2] if lines else None,
                                "over": fair[0], "under": fair[1]}

        if rec and teams:
            out[frozenset(teams)] = rec
    return out


def clv_pct(american_price, fair_close_prob) -> float | None:
    """CLV as a fraction: how much better the taken price is than the no-vig
    close. Equivalent to the EV of the bet evaluated at the closing fair
    probability — positive means we beat the close."""
    if american_price is None or fair_close_prob is None:
        return None
    try:
        return round(odds.expected_value(float(fair_close_prob), float(american_price)), 4)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
