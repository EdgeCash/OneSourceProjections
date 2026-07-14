"""Closing-line-value grading for the Game Plays.

CLV is the lowest-variance edge signal: if the price we took consistently beats
the closing consensus, the process has edge regardless of any single result.

We log each game play with the price we took, then compare it to the **closing
consensus** for that event/market/side, read from the repo's committed closing
lines (``data/history/closing_lines/<sport>/<season>.jsonl.gz`` — moneyline /
spread / total, many books × captures per event).
"""

from __future__ import annotations

import gzip
import json
import re
from pathlib import Path
from statistics import median

from . import edge

REPO_ROOT = Path(__file__).resolve().parents[2]
CLOSING_DIR = REPO_ROOT / "data" / "history" / "closing_lines"


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _load_closing(sport: str, season: int) -> dict:
    """(away, home, date, market, side) -> {'odds': consensus_american,
    'line': consensus_line} using the latest capture per event/market/side."""
    path = CLOSING_DIR / sport.lower() / f"{season}.jsonl.gz"
    if not path.exists():
        return {}

    # Gather rows per key, tracking the latest captured_at.
    grouped: dict = {}
    with gzip.open(path, "rt") as f:
        for ln in f:
            if not ln.strip():
                continue
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            date = str(r.get("scheduled_start") or "")[:10]
            key = (_slug(r.get("away_team")), _slug(r.get("home_team")), date,
                   r.get("market"), r.get("side"))
            grouped.setdefault(key, []).append(r)

    out: dict = {}
    for key, rows in grouped.items():
        latest = max(r.get("captured_at") or "" for r in rows)
        close = [r for r in rows if (r.get("captured_at") or "") == latest]
        odds = [r["american_odds"] for r in close if r.get("american_odds") is not None]
        lines = [r["line"] for r in close if r.get("line") is not None]
        if not odds:
            continue
        out[key] = {"odds": median(odds),
                    "line": median(lines) if lines else None}
    return out


_MARKET = {"Moneyline": "moneyline", "Total": "total", "Spread": "spread"}


def grade_games(history_path: str | Path) -> dict:
    """Fill closing_odds / closing_line / clv / beat_close for ungraded game
    plays whose closing lines are available. Returns {graded, beat_close}."""
    path = Path(history_path)
    if not path.exists():
        return {"graded": 0, "beat_close": 0}
    plays = [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]

    closings: dict = {}   # (sport, season) -> index
    counts = {"graded": 0, "beat_close": 0}
    for p in plays:
        if p.get("clv") is not None:
            continue
        market = _MARKET.get(p.get("market"))
        if not market or p.get("odds") is None:
            continue
        date = str(p.get("game_time") or "")[:10]
        if len(date) != 10:
            continue
        sport, season = (p.get("sport") or "").lower(), int(date[:4])
        if (sport, season) not in closings:
            closings[(sport, season)] = _load_closing(sport, season)
        key = (_slug(p.get("away_team")), _slug(p.get("home_team")), date,
               market, p.get("side_key"))
        close = closings[(sport, season)].get(key)
        if not close:
            continue
        our_dec = edge.american_to_decimal(p["odds"])
        close_dec = edge.american_to_decimal(close["odds"])
        if not our_dec or not close_dec:
            continue
        clv = round(our_dec / close_dec - 1, 4)       # price CLV
        # line movement in our favour (totals/spreads): + = we got the better number
        line_delta = None
        if p.get("line") is not None and close.get("line") is not None:
            side = p.get("side_key")
            if side in ("over", "away"):        # want a lower line
                line_delta = round(close["line"] - p["line"], 2)
            elif side in ("under", "home"):     # want a higher line
                line_delta = round(p["line"] - close["line"], 2)
        p["closing_odds"] = close["odds"]
        p["closing_line"] = close["line"]
        p["clv"] = clv
        p["line_delta"] = line_delta
        p["beat_close"] = clv > 0
        counts["graded"] += 1
        counts["beat_close"] += 1 if clv > 0 else 0

    path.write_text("\n".join(json.dumps(p, default=str) for p in plays) + "\n")
    return counts


def summarize_games(history_path: str | Path) -> dict:
    path = Path(history_path)
    if not path.exists():
        return {"graded": 0}
    plays = [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]
    graded = [p for p in plays if p.get("clv") is not None]
    if not graded:
        return {"graded": 0, "pending": len(plays)}
    clvs = [p["clv"] for p in graded]
    beat = sum(1 for p in graded if p.get("beat_close"))
    return {
        "graded": len(graded),
        "beat_close": beat,
        "beat_rate": round(beat / len(graded), 4),
        "avg_clv": round(sum(clvs) / len(clvs), 4),
        "pending": sum(1 for p in plays if p.get("clv") is None),
    }
