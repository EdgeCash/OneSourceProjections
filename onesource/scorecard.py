"""Model-vs-market scorecard — proof the model adds signal, not just echoes it.

A model that only looks good when it agrees with the market is a market
follower. The real test is the subset of games where our model **disagrees**
with the market's de-vigged price: if our win probability is better calibrated
(lower Brier) and more accurate *there*, the model has independent skill worth
betting. If the market is better on disagreements, our edges are noise and we
should lean on the market more (raise MARKET_SHRINK).

Pure over the graded ledger (``results.load_ledger()``), so it unit-tests with
synthetic rows. Each ``model_winprob`` row carries our ``pred_home_wp``, the
realized ``home_won``, and — captured at grading time — the market's de-vigged
``market_home_wp``.
"""

from __future__ import annotations


def _pick(prob: float) -> str:
    return "home" if prob >= 0.5 else "away"


def classified_games(rows: list[dict]) -> list[dict]:
    """``model_winprob`` rows that carry both our prob and the market's,
    annotated with pick agreement and per-side Brier / correctness."""
    out = []
    for r in rows:
        if r.get("market") != "model_winprob":
            continue
        m, k, y = r.get("pred_home_wp"), r.get("market_home_wp"), r.get("home_won")
        if m is None or k is None or y is None:
            continue
        m, k, y = float(m), float(k), int(y)
        mp, kp = _pick(m), _pick(k)
        out.append({
            "date": r.get("date"), "sport": r.get("sport"), "game": r.get("game"),
            "model_wp": m, "market_wp": k, "home_won": y,
            "model_pick": mp, "market_pick": kp, "agree": mp == kp,
            "edge": round(abs(m - k), 4),
            "model_brier": (m - y) ** 2, "market_brier": (k - y) ** 2,
            "model_correct": int((mp == "home") == bool(y)),
            "market_correct": int((kp == "home") == bool(y)),
        })
    return out


def _agg(games: list[dict]) -> dict:
    n = len(games)
    if not n:
        return {"n": 0, "model_brier": None, "market_brier": None,
                "brier_edge": None, "model_acc": None, "market_acc": None}
    mb = sum(g["model_brier"] for g in games) / n
    kb = sum(g["market_brier"] for g in games) / n
    return {
        "n": n,
        "model_brier": round(mb, 4), "market_brier": round(kb, 4),
        "brier_edge": round(kb - mb, 4),   # > 0  => model better calibrated
        "model_acc": round(sum(g["model_correct"] for g in games) / n, 4),
        "market_acc": round(sum(g["market_correct"] for g in games) / n, 4),
    }


def scorecard(rows: list[dict], min_edge: float = 0.0) -> dict:
    """Split graded games into where the model agrees vs disagrees with the
    market and score each. ``min_edge`` (a probability gap) also treats a
    same-side bet with a large prob divergence as a "disagreement"; with the
    default 0 only opposite picks count as disagreements. The headline is the
    ``disagree`` bucket's ``brier_edge`` and accuracy gap — that's the model's
    independent skill."""
    games = classified_games(rows)
    if min_edge > 0:
        disagree = [g for g in games if not g["agree"] or g["edge"] >= min_edge]
        agree = [g for g in games if g["agree"] and g["edge"] < min_edge]
    else:
        disagree = [g for g in games if not g["agree"]]
        agree = [g for g in games if g["agree"]]
    return {"overall": _agg(games), "agree": _agg(agree),
            "disagree": _agg(disagree), "n_games": len(games)}


def bet_scorecard(rows: list[dict]) -> dict:
    """ROI / win-rate / CLV on the model's *bets*, split by whether the model
    disagreed with the market on that game (joined by date+game)."""
    flag = {(g["date"], g["game"]): g["agree"] for g in classified_games(rows)}
    bets = [r for r in rows if "pnl" in r]

    def agg(bs: list[dict]) -> dict:
        n = len(bs)
        if not n:
            return {"n": 0, "roi_pct": None, "win_rate": None, "avg_clv_pct": None}
        pnl = sum(b["pnl"] for b in bs)
        clvs = [b["clv"] for b in bs if b.get("clv") is not None]
        wins = sum(1 for b in bs if b.get("won"))
        return {"n": n, "roi_pct": round(100 * pnl / n, 2),
                "win_rate": round(wins / n, 4),
                "avg_clv_pct": (round(100 * sum(clvs) / len(clvs), 2)
                                if clvs else None)}

    return {
        "contrarian": agg([b for b in bets
                           if flag.get((b.get("date"), b.get("game"))) is False]),
        "with_market": agg([b for b in bets
                            if flag.get((b.get("date"), b.get("game"))) is True]),
        "unclassified": agg([b for b in bets
                             if flag.get((b.get("date"), b.get("game"))) is None]),
    }
