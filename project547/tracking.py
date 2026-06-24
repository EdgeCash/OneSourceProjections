"""Projection history & tracking — accuracy by *source*, across every sport.

Complements ``results.py`` (the money/CLV ledger for modeled bets). This store
answers a different question: **how good is each projection source — our model,
the de-vigged market, BettingPros — and does our adjustment beat the baseline?**
It covers sports we don't model too, so we build a track record on everything
from day one.

Two append-only JSONL stores under ``data/history/tracking/``:
  * projections.jsonl — one pre-game snapshot per (date, event, source)
  * outcomes.jsonl    — one final per (date, event)
``summary()`` joins them into per-source accuracy / Brier / calibration so the
Ledger can show model vs market vs BettingPros side by side.

Pure logic (dedup, join, scoring) is unit-tested; the live snapshot/grade
fetchers are defensive (any error is swallowed) so a feed outage is harmless.
"""

from __future__ import annotations

import json
import logging

from . import config

log = logging.getLogger(__name__)

TRACK_DIR = config.REPO_ROOT / "data" / "history" / "tracking"
PROJ = TRACK_DIR / "projections.jsonl"
OUTC = TRACK_DIR / "outcomes.jsonl"

SOURCES = ("model", "market", "bettingpros", "blend")


# ---------------------------------------------------------------------------
# Storage (append-only, de-duplicated)
# ---------------------------------------------------------------------------

def _load(path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def _append(path, rows: list[dict], keyfn) -> int:
    if not rows:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    seen = {keyfn(r) for r in _load(path)}
    fresh = [r for r in rows if keyfn(r) not in seen]
    with path.open("a") as f:
        for r in fresh:
            f.write(json.dumps(r, default=str) + "\n")
    return len(fresh)


def _pkey(r):
    return (r["date"], str(r["event_id"]), r["source"])


def _okey(r):
    return (r["date"], str(r["event_id"]))


def log_projection(date, sport, event_id, game, source, home_wp,
                   away_wp=None, draw_wp=None) -> int:
    """Snapshot one source's pre-game home win probability for an event."""
    if home_wp is None or source not in SOURCES:
        return 0
    return _append(PROJ, [{
        "date": date, "sport": sport, "event_id": str(event_id), "game": game,
        "source": source, "home_wp": round(float(home_wp), 4),
        "away_wp": round(float(away_wp), 4) if away_wp is not None else None,
        "draw_wp": round(float(draw_wp), 4) if draw_wp is not None else None,
    }], _pkey)


def record_outcome(date, event_id, home_won, home_score=None,
                   away_score=None) -> int:
    return _append(OUTC, [{
        "date": date, "event_id": str(event_id), "home_won": int(home_won),
        "home_score": home_score, "away_score": away_score,
    }], _okey)


# ---------------------------------------------------------------------------
# Summary (pure: inject rows in tests)
# ---------------------------------------------------------------------------

def summary(projections: list[dict] | None = None,
            outcomes: list[dict] | None = None, dates=None) -> dict:
    """Per-source accuracy / Brier over graded events, plus our lift over the
    market baseline. ``dates`` (str or set) restricts the window."""
    projs = _load(PROJ) if projections is None else projections
    outs = _load(OUTC) if outcomes is None else outcomes
    if isinstance(dates, str):
        dates = {dates}
    won = {_okey(o): o["home_won"] for o in outs}

    by_source: dict[str, dict] = {}
    for r in projs:
        if dates is not None and r["date"] not in dates:
            continue
        y = won.get((r["date"], str(r["event_id"])))
        if y is None:
            continue
        p = min(max(float(r["home_wp"]), 1e-6), 1 - 1e-6)
        s = by_source.setdefault(r["source"], {"n": 0, "correct": 0, "brier": 0.0})
        s["n"] += 1
        s["brier"] += (p - y) ** 2
        if (p >= 0.5) == (y == 1):
            s["correct"] += 1

    out = {}
    for src, s in by_source.items():
        n = s["n"]
        out[src] = {"n": n,
                    "accuracy": round(s["correct"] / n, 4) if n else None,
                    "brier": round(s["brier"] / n, 4) if n else None}
    # lift: how much our model/blend beats the market baseline on accuracy
    base = out.get("market", {}).get("accuracy")
    for src in ("model", "blend", "bettingpros"):
        if base is not None and out.get(src, {}).get("accuracy") is not None:
            out[src]["lift_vs_market"] = round(out[src]["accuracy"] - base, 4)
    return {"by_source": out, "graded_events": len(won)}


# ---------------------------------------------------------------------------
# Live snapshot / grade (defensive — never raise)
# ---------------------------------------------------------------------------

def snapshot(date: str) -> int:
    """Log model/market/BettingPros baselines for every event on a date —
    modeled sports (from the archived slate) and unmodeled ESPN leagues."""
    from . import baseline, bp
    n = 0
    # modeled sports: model win prob + market devig + BP baseline
    try:
        from .results import _load_archive
        arch = _load_archive(date) or {}
        for sport, blob in (arch.get("sports") or {}).items():
            for g in blob.get("games", []) or []:
                eid = g.get("game_pk") or g.get("game_id")
                if eid is None:
                    continue
                game = f"{g.get('away_team')} @ {g.get('home_team')}"
                n += log_projection(date, sport, eid, game, "model",
                                    g.get("home_win_prob"))
                mb = baseline.market_baseline(g.get("home_ml"), g.get("away_ml"))
                if mb:
                    n += log_projection(date, sport, eid, game, "market",
                                        mb.home_wp, mb.away_wp)
                bpb = bp.game_baseline(bp.modeled_bp_sport(sport), date,
                                       g.get("home_team", ""), g.get("away_team", ""))
                if bpb:
                    n += log_projection(date, sport, eid, game, "bettingpros",
                                        bpb.home_wp, bpb.away_wp)
    except Exception as e:  # noqa: BLE001
        log.warning("modeled snapshot %s failed: %s", date, e)
    # unmodeled leagues: market + BP baselines
    try:
        from .clients import espn_extra
        for key, (_path, _name, group) in espn_extra.LEAGUES.items():
            try:
                evs = espn_extra.events(key, date)
            except Exception:
                continue
            bp_sport = baseline.bp_sport_for(group)
            for g in evs:
                if not g.get("is_matchup"):
                    continue
                eid, o = g.get("game_id"), g.get("odds") or {}
                home, away = g["home"]["name"], g["away"]["name"]
                game = f"{away} @ {home}"
                mb = baseline.market_baseline(o.get("home_ml"), o.get("away_ml"),
                                              o.get("draw_ml"))
                if mb:
                    n += log_projection(date, g["league"], eid, game, "market",
                                        mb.home_wp, mb.away_wp, mb.draw_wp)
                bpb = bp.game_baseline(bp_sport, date, home, away)
                if bpb:
                    n += log_projection(date, g["league"], eid, game, "bettingpros",
                                        bpb.home_wp, bpb.away_wp)
    except Exception as e:  # noqa: BLE001
        log.warning("unmodeled snapshot %s failed: %s", date, e)
    return n


def grade(date: str) -> int:
    """Record finals for snapshotted events (modeled + unmodeled ESPN leagues)."""
    have = {str(o["event_id"]) for o in _load(OUTC) if o["date"] == date}
    want = {str(r["event_id"]) for r in _load(PROJ) if r["date"] == date}
    pending = want - have
    if not pending:
        return 0
    n = 0
    try:
        from .clients import espn_extra
        from .results import _load_archive, _finals, _match
        # modeled finals via results helpers
        arch = _load_archive(date) or {}
        for sport, blob in (arch.get("sports") or {}).items():
            fins = _finals(sport, date)
            for g in blob.get("games", []) or []:
                eid = str(g.get("game_pk") or g.get("game_id"))
                if eid not in pending:
                    continue
                fin = _match(g, fins)
                if fin and fin.get("home_score") is not None:
                    n += record_outcome(date, eid,
                                        1 if fin["home_score"] > fin["away_score"] else 0,
                                        fin["home_score"], fin["away_score"])
        # unmodeled finals from ESPN league scoreboards
        for key in espn_extra.LEAGUES:
            try:
                evs = espn_extra.events(key, date)
            except Exception:
                continue
            for g in evs:
                eid = str(g.get("game_id"))
                if eid not in pending or g.get("state") != "post" or not g.get("is_matchup"):
                    continue
                hs, as_ = g["home"].get("score"), g["away"].get("score")
                try:
                    hs, as_ = int(hs), int(as_)
                except (TypeError, ValueError):
                    continue
                n += record_outcome(date, eid, 1 if hs > as_ else 0, hs, as_)
    except Exception as e:  # noqa: BLE001
        log.warning("grade %s failed: %s", date, e)
    return n
