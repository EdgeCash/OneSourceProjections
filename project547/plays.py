"""First-qualify DFS play log: forward-tracking for PrizePicks/Underdog legs.

The moment a prop leg first clears the edge bar (DFS_MIN_EDGE), it's logged once
with the line/odds/model-prob we'd have taken — being early is the edge, so we
commit on first qualify rather than at lock. Each later hourly run refreshes the
*closing* line on still-open legs (for line-movement CLV) but never re-logs them.
After games finish, legs are graded hit/miss from the box-score logs.

Files:
  data/track/dfs_plays.jsonl   one row per logged leg, rewritten in place so a
                               leg's closing line / grade can be filled in.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from . import config, dfs
from .names import normalize

log = logging.getLogger(__name__)

LOG = config.REPO_ROOT / "data" / "track" / "dfs_plays.jsonl"
DFS_SOURCES = ("PrizePicks", "Underdog")


def _key(r: dict) -> tuple:
    return (r["date"], r["sport"], normalize(r.get("player", "")),
            r.get("market", ""), r.get("side", ""))


def load_plays() -> list[dict]:
    if not LOG.exists():
        return []
    return [json.loads(x) for x in LOG.read_text().splitlines() if x.strip()]


def _write(plays: list[dict]) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("w") as f:
        for r in plays:
            f.write(json.dumps(r, default=str) + "\n")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log_qualifying(date: str, day_blob: dict,
                   min_edge: float | None = None,
                   cap: float | None = None) -> list[dict]:
    """Log newly-qualifying DFS legs for ``date`` and refresh closing lines on
    legs already logged. ``day_blob`` is the {sport: {props, games}} slate.
    Returns only the rows logged for the first time this run (what to notify)."""
    min_edge = config.DFS_MIN_EDGE if min_edge is None else min_edge
    cands = dfs.candidates(day_blob, cap=cap or dfs.PROB_CAP)
    if cands.empty:
        return []
    # current line per (sport, player, market) for closing-line refresh
    current = {(r["sport"], normalize(r["player"]), r["market"]): r
              for _, r in cands.iterrows()}

    plays = load_plays()
    by_key = {_key(p): p for p in plays}
    now = _now()

    # refresh closing line on still-open (ungraded) legs
    for p in plays:
        if p.get("graded_at"):
            continue
        cur = current.get((p["sport"], normalize(p.get("player", "")),
                           p.get("market", "")))
        if cur is not None:
            p["close_line"] = float(cur["line"])
            p["close_model_prob"] = _f(cur.get("raw_prob"))
            p["last_seen"] = now

    new_rows: list[dict] = []
    pool = cands[cands["edge"].notna() & (cands["edge"] >= min_edge)]
    for _, r in pool.iterrows():
        if r.get("line_source") not in DFS_SOURCES:
            continue  # only log legs priced off a real PrizePicks/Underdog line
        rec = {
            "date": date, "logged_at": now, "last_seen": now,
            "sport": r["sport"], "player": r["player"], "team": r.get("team", ""),
            "market": r["market"], "side": r["side"], "line": float(r["line"]),
            "line_source": r.get("line_source"),
            "model_prob": _f(r.get("raw_prob")), "book_prob": _f(r.get("book_prob")),
            "edge": _f(r.get("edge")),
            "smash": bool(_f(r.get("edge")) is not None
                          and _f(r.get("edge")) >= config.DFS_SMASH_EDGE),
            "close_line": float(r["line"]), "close_model_prob": _f(r.get("raw_prob")),
            "played": False,  # set True once the leg lands in a notified +EV card
            "actual": None, "won": None, "push": None, "graded_at": None,
        }
        if _key(rec) in by_key:
            continue
        by_key[_key(rec)] = rec
        plays.append(rec)
        new_rows.append(rec)

    _write(plays)
    if new_rows:
        log.info("logged %d new first-qualify legs for %s", len(new_rows), date)
    return new_rows


def line_clv(play: dict) -> float | None:
    """How far the DFS line moved in our favor between first-qualify and close.
    Positive = the number got softer for our side (we were early/right)."""
    o, c = play.get("line"), play.get("close_line")
    if o is None or c is None:
        return None
    return round((o - c) if play.get("side") == "Over" else (c - o), 3)


def grade_plays(asof: str, days: int = 4) -> int:
    """Grade ungraded legs whose games have finished, from the box-score logs.
    Idempotent and resilient (re-checks a window), like results.grade_recent."""
    from datetime import date as _d, timedelta

    from . import playerlogs

    window = {(_d.fromisoformat(asof) - timedelta(days=i)).isoformat()
              for i in range(days)}
    plays = load_plays()
    graded = 0
    for p in plays:
        if p.get("graded_at") or p["date"] not in window:
            continue
        actual = playerlogs.actual_value(p["sport"], p.get("player", ""),
                                         p.get("market", ""), p["date"])
        if actual is None:
            continue  # box score not in yet — try again next run
        line = float(p["line"])
        if actual == line:
            p.update(actual=actual, push=True, won=None, graded_at=_now())
        else:
            over_hit = actual > line
            p.update(actual=actual, push=False, graded_at=_now(),
                     won=bool(over_hit if p.get("side") == "Over" else not over_hit))
        graded += 1
    if graded:
        _write(plays)
        log.info("graded %d DFS legs", graded)
    return graded


def _date_slips(date: str, min_edge: float, max_per_team: int = 2) -> list[dict]:
    """All candidate slips built from this date's still-open logged legs."""
    import pandas as pd

    legs = [p for p in load_plays() if p["date"] == date and not p.get("graded_at")]
    if not legs:
        return []
    df = pd.DataFrame([{
        "sport": p["sport"], "player": p["player"], "team": p.get("team", ""),
        "market": p["market"], "line": p["line"], "side": p["side"],
        "prob": min(p["model_prob"], dfs.PROB_CAP) if p.get("model_prob") else None,
        "raw_prob": p.get("model_prob"), "book_prob": p.get("book_prob"),
        "edge": p.get("edge"), "line_source": p.get("line_source"),
    } for p in legs])
    return dfs.best_slips(df, max_per_team=max_per_team, min_edge=min_edge)


def _card_ev(card: dict) -> float | None:
    return max([x for x in (card.get("power_ev"), card.get("flex_ev"))
               if x is not None], default=None)


def todays_card(date: str, min_edge: float | None = None,
                max_per_team: int = 2) -> dict | None:
    """The largest slip from this date's logged legs (for display)."""
    slips = _date_slips(date, config.DFS_MIN_EDGE if min_edge is None else min_edge,
                        max_per_team)
    return slips[-1] if slips else None


def playable_card(date: str, min_edge: float | None = None,
                  max_per_team: int = 2) -> dict | None:
    """The best **+EV** slip from this date's logged legs — the card actually
    worth putting in. Returns None when no slip clears positive EV after the
    DFS multiplier (the only ones worth playing)."""
    slips = _date_slips(date, config.DFS_MIN_EDGE if min_edge is None else min_edge,
                        max_per_team)
    pos = [(s, _card_ev(s)) for s in slips
           if _card_ev(s) is not None and _card_ev(s) > 0]
    return max(pos, key=lambda x: x[1])[0] if pos else None


def _leg_key(date: str, leg: dict) -> str:
    return (f"dfs|{date}|{leg.get('sport', '')}|"
            f"{normalize(leg.get('player', ''))}|{leg.get('market', '')}|"
            f"{leg.get('side', '')}")


def mark_played(date: str, card: dict) -> int:
    """Flag the card's legs as played (what we actually wager and track).
    Returns the number of legs newly flagged."""
    keys = {_leg_key(date, l) for l in card.get("legs", [])}
    plays = load_plays()
    n = 0
    for p in plays:
        if not p.get("played") and _leg_key(p["date"], p) in keys:
            p["played"] = True
            n += 1
    if n:
        _write(plays)
    return n


# ---------------------------------------------------------------------------
# Game (moneyline / total) plays — extracted from the slate for notification.
# The results ledger grades every EV>=MIN_EDGE bet; this surfaces the CLV-
# validated "sharp" band (moderate EV) to push at first qualify, and tags the
# high-EV ones "verify" (their CLV says the line is probably stale, not soft).
# ---------------------------------------------------------------------------

def game_play_candidates(date: str, day_blob: dict,
                         min_edge: float | None = None) -> list[dict]:
    """Moneyline/total bets in ``day_blob`` with model EV >= ``min_edge``
    (default SHARP_EV_MIN). Each carries a ``tier`` — 'sharp' (in the validated
    band, push-worthy), 'stale' (EV too high, likely a stale line -> verify), or
    'watch' (between) — a ``gate`` status (the market's demonstrated-edge verdict
    from realized CLV), and a dedup key. Plays in a GATED market (proven no edge)
    are dropped; only CLEARED markets can be 'sharp' (pushed) — a PROBATION market
    is surfaced for tracking but never headline-pushed until it proves out."""
    from . import edge_gate
    min_edge = config.SHARP_EV_MIN if min_edge is None else min_edge
    gates = edge_gate.gate_table()
    bands = edge_gate.ev_bands()   # per-market EV bands (global fallback per market)
    seed = edge_gate.conviction_prior()   # backtest CLV prior (empty unless enabled)

    def _conv_stat(key):
        # gate/stake come from live only; conviction may be seed-enriched while
        # the live CLV for this market is still thin (self-retiring prior).
        return edge_gate.blend_conviction(gates.get(key), seed.get(key))

    out = []
    for sport, blob in (day_blob or {}).items():
        for g in blob.get("games", []) or []:
            label = f"{g.get('away_team')} @ {g.get('home_team')}"
            for side in ("home", "away"):
                price = g.get(f"{side}_ml")
                ev = g.get(f"{side}_ml_ev", g.get(f"{side}_ev"))
                if price and ev is not None and ev >= min_edge:
                    out.append(_game_play(date, sport, label, "moneyline", side,
                                          g.get(f"{side}_team", side), price, ev,
                                          gate=edge_gate.status_for(sport, "moneyline", gates),
                                          stat=_conv_stat((sport, "moneyline")),
                                          band=bands.get((sport, "moneyline"))))
            line = g.get("total_line")
            for side, odds_key, ev_key in (("over", "over_odds", "over_ev"),
                                           ("under", "under_odds", "under_ev")):
                price, ev = g.get(odds_key), g.get(ev_key)
                if line is not None and price and ev is not None and ev >= min_edge:
                    out.append(_game_play(date, sport, label, "total", side,
                                          f"{side} {line:g}", price, ev, line=line,
                                          gate=edge_gate.status_for(sport, "total", gates),
                                          stat=_conv_stat((sport, "total")),
                                          band=bands.get((sport, "total"))))
    # rank the board by proven edge (conviction), not raw EV, so a consumer that
    # takes the top plays leads with the markets we most reliably beat the close
    # in. EV breaks ties. GATED (no-edge) markets are dropped.
    curated = [c for c in out if c["curatable"]]
    curated.sort(key=lambda c: (c.get("conviction") or 0.0, c.get("ev") or 0.0),
                 reverse=True)
    return curated


def _tier(ev: float, band: tuple | None = None) -> str:
    lo, hi = band if band else (config.SHARP_EV_MIN, config.SHARP_EV_MAX)
    if ev >= config.STALE_EV:
        return "stale"          # EV too high -> line likely stale, verify
    if lo <= ev <= hi:
        return "sharp"          # the CLV-validated sweet spot -> push
    return "watch"              # outside the band but below the stale cutoff


def _game_play(date, sport, game, market, side, pick, price, ev, line=None,
               gate: str = "probation", stat: dict | None = None,
               band: tuple | None = None) -> dict:
    from . import edge_gate
    tier = _tier(float(ev), band)
    # a play is only "sharp" (pushed) when its EV is in-band AND the market has a
    # demonstrated edge (gate CLEARED). PROBATION/GATED never headline-push.
    sharp = tier == "sharp" and gate == edge_gate.CLEARED
    # expected_clv = the market's rolling mean CLV (how much this play is
    # estimated to beat the close); conviction = the confidence-discounted,
    # in-band rank key the board sorts on. GATED markets score 0 defensively
    # (they're dropped by the curatable filter anyway).
    expected_clv = (stat or {}).get("avg_clv") if stat else None
    conviction = (0.0 if gate == edge_gate.GATED
                  else edge_gate.conviction(float(ev), sport, market, stat, band))
    return {"key": f"game|{date}|{sport}|{game}|{market}|{side}",
            "date": date, "sport": sport, "game": game, "market": market,
            "side": side, "pick": pick, "price": price, "ev": round(float(ev), 4),
            "line": line, "tier": tier, "gate": gate,
            "expected_clv": expected_clv, "conviction": conviction,
            "curatable": edge_gate.is_curatable(gate),
            "stake_mult": edge_gate.stake_mult(gate),
            "sharp": sharp, "verify": tier == "stale"}


# ---------------------------------------------------------------------------
# Notification dedup: keys already pushed, so we never re-ping the same play.
# ---------------------------------------------------------------------------

NOTIFIED = config.REPO_ROOT / "data" / "track" / "notified_keys.json"


def load_notified() -> set:
    if not NOTIFIED.exists():
        return set()
    try:
        return set(json.loads(NOTIFIED.read_text()))
    except Exception:
        return set()


def mark_notified(keys) -> None:
    NOTIFIED.parent.mkdir(parents=True, exist_ok=True)
    NOTIFIED.write_text(json.dumps(sorted(load_notified() | set(keys))))


# ---------------------------------------------------------------------------
# One-tap confirm: each pushed play carries Played/Skip buttons. The button
# POSTs "played <id>" / "skip <id>" to the confirm topic; the job polls it and
# applies the decision. Only confirmed-Played plays count toward the record.
# ---------------------------------------------------------------------------

PENDING = config.REPO_ROOT / "data" / "track" / "pending_plays.json"
CONFIRM = config.REPO_ROOT / "data" / "track" / "confirmations.json"


def _load_json(path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def play_id(date: str, keys) -> str:
    """Stable short id for a play (a card or a game bet), from its leg keys."""
    import hashlib
    h = hashlib.sha1((date + "|" + "|".join(sorted(keys))).encode()).hexdigest()
    return h[:8]


def register_pending(kind: str, date: str, keys) -> str:
    """Record a pushed play so a later Played/Skip tap can be resolved to its
    keys. Returns the play id to embed in the notification buttons."""
    keys = sorted(set(keys))
    pid = play_id(date, keys)
    pending = _load_json(PENDING, {})
    pending[pid] = {"kind": kind, "date": date, "keys": keys,
                    "pushed_at": _now()}
    PENDING.parent.mkdir(parents=True, exist_ok=True)
    PENDING.write_text(json.dumps(pending, default=str))
    return pid


def confirmations() -> dict:
    c = _load_json(CONFIRM, {})
    return {"played": set(c.get("played", [])), "skipped": set(c.get("skipped", []))}


def confirmed_played() -> set:
    return confirmations()["played"]


def confirmed_skipped() -> set:
    return confirmations()["skipped"]


def apply_confirmations(messages: list[str]) -> dict:
    """Parse Played/Skip button taps ('played <id>' / 'skip <id>'), resolve each
    id to its keys via the pending store, and record the decision. Marks the
    matching DFS legs played. Idempotent. Returns {'played': n, 'skipped': n}."""
    pending = _load_json(PENDING, {})
    c = confirmations()
    played, skipped = c["played"], c["skipped"]
    n_p = n_s = 0
    for body in messages or []:
        parts = str(body).strip().split()
        if len(parts) < 2 or parts[0] not in ("played", "skip"):
            continue
        info = pending.get(parts[1])
        if not info:
            continue
        keys = set(info.get("keys", []))
        if parts[0] == "played":
            new = keys - played
            played |= keys
            skipped -= keys
            n_p += len(new)
        else:
            new = keys - skipped
            skipped |= keys
            played -= keys
            n_s += len(new)
    if n_p or n_s:
        CONFIRM.parent.mkdir(parents=True, exist_ok=True)
        CONFIRM.write_text(json.dumps({"played": sorted(played),
                                       "skipped": sorted(skipped)}))
        # reflect played status onto the DFS legs themselves
        rows = load_plays()
        changed = False
        for p in rows:
            want = _leg_key(p["date"], p) in played
            if bool(p.get("played")) != want:
                p["played"] = want
                changed = True
        if changed:
            _write(rows)
    if n_p or n_s:
        log.info("applied confirmations: +%d played, +%d skipped", n_p, n_s)
    return {"played": n_p, "skipped": n_s}


def _win_rate(rows: list[dict]) -> float | None:
    graded = [p for p in rows if p.get("graded_at") and p.get("push") is not True]
    if not graded:
        return None
    return round(sum(1 for p in graded if p.get("won")) / len(graded), 4)


def summary() -> dict:
    """DFS leg track record. Reports the full logged pool and, separately, the
    legs we actually played (those in a notified +EV card) — the set that
    matters. Card ROI is variance-heavy and derived; legs are the signal."""
    rows = load_plays()
    played = [p for p in rows if p.get("played")]
    graded = [p for p in rows if p.get("graded_at") and p.get("push") is not True]
    played_graded = [p for p in played
                     if p.get("graded_at") and p.get("push") is not True]
    clvs = [c for c in (line_clv(p) for p in played) if c is not None]
    return {
        "legs_logged": len(rows),
        "legs_graded": len(graded),
        "leg_win_rate": _win_rate(rows),
        "played_logged": len(played),
        "played_graded": len(played_graded),
        "played_win_rate": _win_rate(played),
        "avg_line_clv": round(sum(clvs) / len(clvs), 3) if clvs else None,
        "line_clv_beat_rate": round(sum(1 for c in clvs if c > 0) / len(clvs), 4)
        if clvs else None,
    }


def played_accuracy(dates=None) -> tuple[float | None, int]:
    """Win rate across everything you confirmed Played — DFS legs and game bets
    combined (pushes/ties excluded). Returns (accuracy, n_decided). Pass
    ``dates`` (a date string or set) to restrict to specific slate dates, e.g.
    yesterday for the daily recap; omit for the cumulative number."""
    if isinstance(dates, str):
        dates = {dates}
    wins = n = 0
    for p in load_plays():
        if (p.get("played") and p.get("graded_at") and p.get("push") is not True
                and (dates is None or p.get("date") in dates)):
            n += 1
            wins += 1 if p.get("won") else 0
    played = confirmed_played()
    if played:
        from . import results
        for r in results.load_ledger():
            if "pnl" not in r or (dates is not None and r.get("date") not in dates):
                continue
            key = (f"game|{r['date']}|{r['sport']}|{r['game']}|"
                   f"{r['market']}|{r.get('side', '')}")
            if key in played:
                n += 1
                wins += 1 if r.get("won") else 0
    return (round(wins / n, 4) if n else None, n)


DAILY = config.REPO_ROOT / "data" / "track" / "daily_record.jsonl"


def record_daily(date: str, model: tuple, played: tuple) -> dict:
    """Upsert one day's recap line (model + played accuracy) into the daily
    record log, keyed by date. Returns the stored row."""
    rows = load_daily_record()
    by_date = {r["date"]: r for r in rows}
    by_date[date] = {
        "date": date,
        "model_acc": model[0], "model_n": model[1],
        "played_acc": played[0], "played_n": played[1],
        "recorded_at": _now(),
    }
    DAILY.parent.mkdir(parents=True, exist_ok=True)
    with DAILY.open("w") as f:
        for d in sorted(by_date):
            f.write(json.dumps(by_date[d], default=str) + "\n")
    return by_date[date]


def load_daily_record() -> list[dict]:
    if not DAILY.exists():
        return []
    return [json.loads(x) for x in DAILY.read_text().splitlines() if x.strip()]


def _f(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None
