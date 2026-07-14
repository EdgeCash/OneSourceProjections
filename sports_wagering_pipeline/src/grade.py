"""Grade logged picks against actual results — the track record.

Reads the committed pick log (``picks_history.jsonl``) and fills in each pick's
``result`` (win / loss / push) by looking up the player's *actual* stat for that
date in the repo's committed player game logs
(``data/history/playerlogs/<sport>.jsonl``). Then summarizes the record — overall,
by confidence bucket (does higher confidence really hit more?), by operator, and
head-to-head vs BettingPros' recommended side (do we beat BP when we disagree?).

Result grading is the honest proof: not "trust the model", but "here is what the
picks actually did." CLV grading of the game plays (against
``data/history/closing_lines``) is the next layer.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAYERLOGS = REPO_ROOT / "data" / "history" / "playerlogs"

# stat_type (as logged) -> field in the player game log, per sport.
STAT_FIELD = {
    "MLB": {"Hits": "hits", "Total Bases": "totalBases",
            "Home Runs": "homeRuns", "Strikeouts": "strikeOuts"},
    "WNBA": {"Points": "points", "Rebounds": "rebounds", "Assists": "assists",
             "3-Pointers Made": "three_made"},
    "NBA": {"Points": "points", "Rebounds": "rebounds", "Assists": "assists",
            "3-Pointers Made": "three_made"},
}


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _load_playerlog(sport: str) -> dict:
    """(player-slug, date) -> stat row, from the committed game log."""
    path = PLAYERLOGS / f"{sport.lower()}.jsonl"
    idx: dict = {}
    if not path.exists():
        return idx
    for ln in path.read_text().splitlines():
        if not ln.strip():
            continue
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        name, date = r.get("name"), r.get("date")
        if name and date:
            idx[(_slug(name), date)] = r
    return idx


def grade_history(history_path: str | Path) -> dict:
    """Fill `result`/`actual` for ungraded picks whose games are final.
    Rewrites the file; returns {graded, wins, losses, pushes}."""
    path = Path(history_path)
    if not path.exists():
        return {"graded": 0, "wins": 0, "losses": 0, "pushes": 0}

    picks = [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]
    logs: dict = {}   # sport -> index (lazy)
    counts = {"graded": 0, "wins": 0, "losses": 0, "pushes": 0}

    for p in picks:
        if p.get("result"):
            continue
        sport = (p.get("sport") or "").upper()
        field = STAT_FIELD.get(sport, {}).get(p.get("stat"))
        if not field:
            continue  # stat we can't grade from the logs yet
        if sport not in logs:
            logs[sport] = _load_playerlog(sport)
        row = logs[sport].get((_slug(p.get("player")), p.get("date")))
        if not row or row.get(field) is None:
            continue  # game not final / no log row yet
        actual = row[field]
        line, side = p.get("line"), p.get("side")
        if actual == line:
            result = "push"
        elif (actual > line) == (side == "OVER"):
            result = "win"
        else:
            result = "loss"
        p["result"] = result
        p["actual"] = actual
        counts["graded"] += 1
        counts[{"win": "wins", "loss": "losses", "push": "pushes"}[result]] += 1

    path.write_text("\n".join(json.dumps(p, default=str) for p in picks) + "\n")
    return counts


# --------------------------------------------------------------------------- #
# Summary for the Track Record tab
# --------------------------------------------------------------------------- #
def _rate(wins: int, losses: int) -> float | None:
    n = wins + losses
    return round(wins / n, 4) if n else None


def summarize(history_path: str | Path) -> dict:
    """Aggregate graded picks: overall, by confidence bucket, by operator, and
    head-to-head vs BettingPros' recommended side."""
    path = Path(history_path)
    if not path.exists():
        return {"graded": 0}
    picks = [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]
    graded = [p for p in picks if p.get("result") in ("win", "loss", "push")]

    def tally(rows):
        w = sum(1 for r in rows if r["result"] == "win")
        l = sum(1 for r in rows if r["result"] == "loss")
        pu = sum(1 for r in rows if r["result"] == "push")
        return {"n": len(rows), "w": w, "l": l, "p": pu, "hit": _rate(w, l)}

    buckets = [("80-100", 80, 101), ("70-79", 70, 80),
               ("60-69", 60, 70), ("<60", -1, 60)]
    by_conf = []
    for label, lo, hi in buckets:
        rows = [p for p in graded if lo <= (p.get("confidence") or 0) < hi]
        if rows:
            by_conf.append({"bucket": label, **tally(rows)})

    ops = sorted({p.get("operator") for p in graded if p.get("operator")})
    by_op = [{"operator": op, **tally([p for p in graded if p.get("operator") == op])}
             for op in ops]

    # Head-to-head vs BP's recommended side.
    def _agree(p):
        rec = (p.get("bp_recommended") or "").upper()
        return rec in ("OVER", "UNDER") and rec == p.get("side")
    agree_rows = [p for p in graded if p.get("bp_recommended") and _agree(p)]
    disagree_rows = [p for p in graded if p.get("bp_recommended") and not _agree(p)]

    return {
        "graded": len(graded),
        "overall": tally(graded),
        "by_confidence": by_conf,
        "by_operator": by_op,
        "vs_bp": {"agree": tally(agree_rows), "disagree": tally(disagree_rows)},
        "pending": sum(1 for p in picks if not p.get("result")),
    }
