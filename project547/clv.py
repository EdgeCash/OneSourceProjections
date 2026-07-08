"""Closing Line Value (CLV) — the lowest-variance proxy for betting skill.

The hourly snapshot store (data/history/snapshots/<sport>/<date>.jsonl) keeps
a timestamped series of BettingPros odds; the latest capture before a game is
that game's closing line. We de-vig it to a fair probability and compare it to
the price each recommended bet was made at: if the model was getting a better
price than the no-vig close, that's positive CLV — the strongest early signal
that an edge is real, long before win/loss ROI converges.

De-vig discipline (audit #18/#22):
  - Each two-way pair is de-vigged **within one book at one line** — never a
    best-over from book A paired with a best-under from book B (that pooling
    understates the hold and, at different lines, prices a different event).
  - Lined markets (totals, spreads) build their consensus only from books
    quoting the **modal closing line**, and that line is recorded in the
    output so grading can refuse to score a bet at line L against fair-at-M.
  - The **power** de-vig method is used (calculators.no_vig): multiplicative
    normalization flatters the market baseline at the extremes
    (favorite-longshot bias), which would make our CLV look better than it is.
"""

from __future__ import annotations

import gzip
import json
from collections import Counter, defaultdict

from . import calculators, config, odds
from .names import normalize

SNAP_DIR = config.REPO_ROOT / "data" / "history" / "snapshots"

# sanity band for a two-way pair's raw implied sum (the book's hold): outside
# this the quotes are stale/mismatched and would manufacture a phantom fair.
VIG_SUM_MIN, VIG_SUM_MAX = 0.98, 1.30


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


def _devig_pair(a_american, b_american) -> float | None:
    """Fair P(side a) from one book's two-way pair (power method). Returns
    None when the pair is incoherent (implied sum outside the hold band)."""
    try:
        pa = odds.implied_prob(float(a_american))
        pb = odds.implied_prob(float(b_american))
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    if not (VIG_SUM_MIN <= pa + pb <= VIG_SUM_MAX):
        return None
    fa, _ = calculators.no_vig(float(a_american), float(b_american),
                               method="power")
    return fa


def _modal_line(lines: list) -> float | None:
    """The line quoted most often; ties broken by the median of the tied
    values. None when no lines."""
    lines = [l for l in lines if l is not None]
    if not lines:
        return None
    counts = Counter(lines)
    top = max(counts.values())
    tied = sorted(l for l, n in counts.items() if n == top)
    return tied[len(tied) // 2]


def _group_fair(ers: list[dict]) -> tuple[frozenset | None, dict]:
    """De-vigged fair probs for one source-event's latest capture.

    Identifies the game by its two moneyline teams, then de-vigs each market
    per book (both sides from the SAME book at the SAME line), and averages
    the per-book fair probs across the books quoting the modal line."""
    last = max((r.get("captured_at") or "") for r in ers)
    ers = [r for r in ers if (r.get("captured_at") or "") == last]

    # --- moneyline: {book: {team: price}}, de-vig per book, average ---
    ml_books: dict = defaultdict(dict)
    teams_seen: set = set()
    for r in ers:
        if r.get("market") == "moneyline" and r.get("participant") \
                and r.get("odds") is not None:
            t = normalize(r["participant"])
            teams_seen.add(t)
            ml_books[r.get("book_id")][t] = r["odds"]
    if len(teams_seen) != 2:
        return None, {}
    ta, tb = sorted(teams_seen)
    teams = frozenset(teams_seen)
    rec: dict = {}
    ml_fairs = []
    for prices in ml_books.values():
        if ta in prices and tb in prices:
            fa = _devig_pair(prices[ta], prices[tb])
            if fa is not None:
                ml_fairs.append(fa)
    if ml_fairs:
        fa = sum(ml_fairs) / len(ml_fairs)
        rec["moneyline"] = {ta: fa, tb: 1 - fa}

    # --- totals: {(book, line): {side: price}}, modal line across books ---
    tot: dict = defaultdict(dict)
    for r in ers:
        if r.get("market") != "total" or r.get("odds") is None:
            continue
        side = str(r.get("selection", "")).lower()
        side = ("over" if "over" in side
                else "under" if "under" in side else None)
        if side is None or r.get("line") is None:
            continue
        tot[(r.get("book_id"), float(r["line"]))][side] = r["odds"]
    by_line: dict = defaultdict(list)
    for (_book, line), sides in tot.items():
        if "over" in sides and "under" in sides:
            fo = _devig_pair(sides["over"], sides["under"])
            if fo is not None:
                by_line[line].append(fo)
    if by_line:
        line = _modal_line([l for l, fs in by_line.items() for _ in fs])
        fos = by_line[line]
        rec["total"] = {"line": line, "over": sum(fos) / len(fos),
                        "under": 1 - sum(fos) / len(fos)}

    # --- spreads: per book, the two teams at mirrored lines ---
    sp: dict = defaultdict(dict)
    for r in ers:
        if r.get("market") == "spread" and r.get("participant") \
                and r.get("odds") is not None and r.get("line") is not None:
            sp[r.get("book_id")][normalize(r["participant"])] = (
                float(r["line"]), r["odds"])
    sp_by_line: dict = defaultdict(list)
    for prices in sp.values():
        if ta in prices and tb in prices:
            la, oa = prices[ta]
            lb, ob = prices[tb]
            if la != -lb:          # not the same market (alt line mix-up)
                continue
            fa = _devig_pair(oa, ob)
            if fa is not None:
                sp_by_line[la].append(fa)
    if sp_by_line:
        la = _modal_line([l for l, fs in sp_by_line.items() for _ in fs])
        fas = sp_by_line[la]
        fa = sum(fas) / len(fas)
        rec["spread"] = {ta: {"line": la, "prob": fa},
                         tb: {"line": -la, "prob": 1 - fa}}
    return teams, rec


def closing_lines(sport: str, date: str, snap_dir=None) -> dict:
    """Per-game de-vigged closing probabilities from the snapshot store.

    Merges every captured source (BettingPros + The Odds API) into one
    market-consensus close per game: each source-event is de-vigged per book
    at the modal line on its own latest capture, then averaged across sources
    by matchup (again only across sources quoting the modal line, so the
    consensus fair prob always describes ONE line — recorded in the output).
    Returns {frozenset({norm_home, norm_away}): {
        "moneyline": {norm_team: prob},
        "total": {"line": x, "over": p, "under": 1-p},
        "spread": {norm_team: {"line": l, "prob": p}}}}.
    """
    rows = [r for r in _load_rows(sport, date, snap_dir) if r.get("kind") == "game"]
    if not rows:
        return {}

    # group by (source, event) so totals associate with the event's teams
    groups: dict = defaultdict(list)
    for r in rows:
        groups[(r.get("source", "bp"), r.get("event_id"))].append(r)

    # collect each source's fair probs per matchup, then average them
    ml_acc: dict = defaultdict(lambda: defaultdict(list))
    tot_acc: dict = defaultdict(list)               # teams -> [(line, over_fair)]
    sp_acc: dict = defaultdict(lambda: defaultdict(list))  # teams -> team -> [(line, prob)]
    for ers in groups.values():
        teams, rec = _group_fair(ers)
        if not teams:
            continue
        for team, p in (rec.get("moneyline") or {}).items():
            ml_acc[teams][team].append(p)
        if rec.get("total") and rec["total"].get("line") is not None:
            tot_acc[teams].append((rec["total"]["line"], rec["total"]["over"]))
        for team, d in (rec.get("spread") or {}).items():
            sp_acc[teams][team].append((d["line"], d["prob"]))

    out: dict = {}
    for teams in set(ml_acc) | set(tot_acc) | set(sp_acc):
        rec: dict = {}
        if ml_acc.get(teams):
            rec["moneyline"] = {t: round(sum(ps) / len(ps), 4)
                                for t, ps in ml_acc[teams].items()}
        if tot_acc.get(teams):
            pairs = tot_acc[teams]
            line = _modal_line([l for l, _ in pairs])
            ovs = [p for l, p in pairs if l == line]
            rec["total"] = {"line": line,
                            "over": round(sum(ovs) / len(ovs), 4),
                            "under": round(1 - sum(ovs) / len(ovs), 4)}
        if sp_acc.get(teams):
            spread = {}
            for team, pairs in sp_acc[teams].items():
                line = _modal_line([l for l, _ in pairs])
                ps = [p for l, p in pairs if l == line]
                spread[team] = {"line": line,
                                "prob": round(sum(ps) / len(ps), 4)}
            rec["spread"] = spread
        if rec:
            out[teams] = rec
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
