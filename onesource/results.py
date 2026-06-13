"""Forward-test bookkeeping: archive each slate's projections, grade games
once they finish, and summarize realized performance.

- Archived projections: data/output/projections/<date>.json (the slate as
  projected, including the market edges at projection time).
- Graded results ledger: data/track/results.jsonl, one row per graded
  bet/game, append-only and de-duplicated by (date, game, market, side).
- Performance summary: computed on demand from the ledger.

Game moneyline/total bets recommended by the model (EV >= MIN_EDGE in the
archived slate) are graded against final scores; the model's win-probability
Brier is tracked on every game regardless of whether a bet was placed.
"""

from __future__ import annotations

import json
import logging

from . import config, odds
from .clients import espn, mlb_statsapi
from .names import normalize

log = logging.getLogger(__name__)

PROJ_DIR = config.OUTPUT_DIR / "projections"
LEDGER = config.REPO_ROOT / "data" / "track" / "results.jsonl"


def archive_projections(date: str, sports_blob: dict):
    """Persist a slate's projections so they can be graded after games end."""
    PROJ_DIR.mkdir(parents=True, exist_ok=True)
    path = PROJ_DIR / f"{date}.json"
    path.write_text(json.dumps({"date": date, "sports": sports_blob}, default=str))


def _load_archive(date: str) -> dict | None:
    path = PROJ_DIR / f"{date}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _finals(sport: str, date: str) -> list[dict]:
    if sport == "MLB":
        return mlb_statsapi.final_scores(date)
    try:
        return espn.results_range(sport, date, date)
    except Exception as e:
        log.warning("%s finals unavailable for %s: %s", sport, date, e)
        return []


def _match(game: dict, finals: list[dict]) -> dict | None:
    gid = game.get("game_pk") or game.get("game_id")
    for f in finals:
        if gid and (f.get("game_pk") == gid or str(f.get("game_id")) == str(gid)):
            return f
    h, a = normalize(game.get("home_team", "")), normalize(game.get("away_team", ""))
    for f in finals:
        fh, fa = normalize(f.get("home_team", "")), normalize(f.get("away_team", ""))
        if (h in fh or fh in h) and (a in fa or fa in a):
            return f
    return None


def _existing_keys() -> set:
    keys = set()
    if LEDGER.exists():
        for line in LEDGER.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                keys.add((r["date"], r["game"], r["market"], r.get("side", "")))
    return keys


def _append(rows: list[dict]):
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a") as f:
        for r in rows:
            f.write(json.dumps(r, default=str) + "\n")


def grade_date(date: str, min_edge: float | None = None) -> int:
    """Grade all completed games for a date against the archived slate.
    Idempotent: skips rows already in the ledger. Returns rows added."""
    min_edge = config.MIN_EDGE if min_edge is None else min_edge
    archive = _load_archive(date)
    if not archive:
        return 0
    seen = _existing_keys()
    new_rows: list[dict] = []

    for sport, blob in archive["sports"].items():
        games = blob.get("games", [])
        if not games:
            continue
        finals = _finals(sport, date)
        if not finals:
            continue
        for g in games:
            fin = _match(g, finals)
            if not fin or fin.get("home_score") is None:
                continue
            hs, as_ = fin["home_score"], fin["away_score"]
            label = f"{g.get('away_team')} @ {g.get('home_team')}"
            home_won = 1 if hs > as_ else 0
            total = hs + as_

            # win-probability tracking (every game, no bet required)
            hwp = g.get("home_win_prob")
            key = (date, label, "model_winprob", "")
            if hwp is not None and key not in seen:
                new_rows.append({
                    "date": date, "sport": sport, "game": label,
                    "market": "model_winprob", "side": "",
                    "pred_home_wp": round(float(hwp), 4), "home_won": home_won,
                    "brier": round((float(hwp) - home_won) ** 2, 4),
                    "proj_total": g.get("proj_total"), "actual_total": total,
                })

            # moneyline bets recommended at projection time
            for side, won in (("home", home_won == 1), ("away", home_won == 0)):
                price = g.get(f"{side}_ml")
                ev = g.get(f"{side}_ml_ev", g.get(f"{side}_ev"))
                k = (date, label, "moneyline", side)
                if price and ev is not None and ev >= min_edge and k not in seen:
                    new_rows.append(_bet_row(date, sport, label, "moneyline", side,
                                             price, won, ev))

            # total bet recommended at projection time
            line = g.get("total_line")
            over_odds = g.get("over_odds")
            over_ev = g.get("over_ev")
            if line is not None and over_odds and over_ev is not None and total != line:
                k = (date, label, "total", "over")
                if over_ev >= min_edge and k not in seen:
                    new_rows.append(_bet_row(date, sport, label, "total", "over",
                                             over_odds, total > line, over_ev,
                                             line=line))

    _append(new_rows)
    if new_rows:
        log.info("graded %d rows for %s", len(new_rows), date)
    return len(new_rows)


def grade_recent(asof: str, days: int = 4, min_edge: float | None = None) -> int:
    """Grade the last ``days`` dates ending at ``asof`` (inclusive).

    Grading is idempotent (de-duped by row key), so re-checking a window
    each run is cheap and makes the forward test resilient: if a run is
    missed, finals post late, or a slate is graded before its games end,
    the next run still catches the day. Returns total rows added.
    """
    from datetime import date as _d, timedelta

    base = _d.fromisoformat(asof)
    total = 0
    for i in range(days):
        d = (base - timedelta(days=i)).isoformat()
        try:
            total += grade_date(d, min_edge=min_edge)
        except Exception as e:
            log.error("grading %s failed: %s", d, e)
    return total


def _bet_row(date, sport, game, market, side, price, won, ev, line=None) -> dict:
    dec = odds.american_to_decimal(float(price))
    return {"date": date, "sport": sport, "game": game, "market": market,
            "side": side, "line": line, "price": price, "ev": round(float(ev), 4),
            "won": bool(won), "pnl": round(dec - 1 if won else -1.0, 4)}


def load_ledger() -> list[dict]:
    if not LEDGER.exists():
        return []
    return [json.loads(x) for x in LEDGER.read_text().splitlines() if x.strip()]


def performance() -> dict:
    """Summarize the ledger: betting record/ROI and model Brier by sport."""
    rows = load_ledger()
    out = {"overall": _summarize(rows)}
    by_sport = {}
    for r in rows:
        by_sport.setdefault(r["sport"], []).append(r)
    out["by_sport"] = {s: _summarize(rs) for s, rs in by_sport.items()}
    return out


def _summarize(rows: list[dict]) -> dict:
    bets = [r for r in rows if "pnl" in r]
    games = [r for r in rows if r["market"] == "model_winprob"]
    staked = len(bets)
    pnl = sum(r["pnl"] for r in bets)
    wins = sum(1 for r in bets if r["won"])
    brier = (sum(r["brier"] for r in games) / len(games)) if games else None
    return {
        "graded_games": len(games),
        "model_brier": round(brier, 4) if brier is not None else None,
        "bets": staked,
        "bet_win_rate": round(wins / staked, 4) if staked else None,
        "units": round(pnl, 2),
        "roi_pct": round(100 * pnl / staked, 2) if staked else None,
    }
