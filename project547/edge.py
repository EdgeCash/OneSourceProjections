"""Market-consensus edge engine — the sharp layer the elite tools are built on.

Single-book EV (our model vs one BettingPros price) is noisy. The professional
standard is to measure edge against the **de-vigged market consensus**: take
every book's price on a market, strip the vig from each, average the fair
probabilities, and call a bet +EV only when the best available price beats that
consensus. That's the core of OddsJam / Unabated. On top of consensus we scan
the slate for **arbitrage**, **middles**, and **low-hold** opportunities.

Everything here is a pure function over ``{book: {side: american_price}}`` dicts
(or, for middles, a list of offers), so it unit-tests without the dashboard or
any live API. The snapshot-store adapter at the bottom turns the captured
multi-book odds into those dicts.
"""

from __future__ import annotations

from collections import defaultdict

from . import calculators, odds


# ---------------------------------------------------------------------------
# Consensus fair value (de-vig every book, average)
# ---------------------------------------------------------------------------

def market_consensus(books: dict, sides: list[str], method: str = "power",
                     exclude_book: str | None = None) -> dict | None:
    """No-vig consensus fair probability per side across books.

    ``books`` is ``{book_id: {side: american_price}}``. For each book that
    prices *every* side, de-vig that book's prices into fair probabilities; the
    consensus is the average of those per-book fair probabilities. ``method`` is
    the de-vig method (``power`` corrects favorite-longshot bias — the sharp
    default). ``exclude_book`` drops one book from the consensus (used so a
    price isn't graded against itself).

    Returns ``{"fair": {side: prob}, "n_books": n}`` or ``None`` if no book
    prices the full market.
    """
    fairs: dict = defaultdict(list)
    n = 0
    for book, prices in books.items():
        if book == exclude_book:
            continue
        if not all(prices.get(s) is not None for s in sides):
            continue
        try:
            ps = calculators.no_vig(*[prices[s] for s in sides], method=method)
        except (TypeError, ValueError, ZeroDivisionError):
            continue
        for s, p in zip(sides, ps):
            fairs[s].append(p)
        n += 1
    if not n:
        return None
    return {"fair": {s: sum(v) / len(v) for s, v in fairs.items()}, "n_books": n}


def best_prices(books: dict, sides: list[str]) -> dict:
    """Best (highest, i.e. most favorable) American price per side across books:
    ``{side: {"price": american, "book": book_id}}`` (sides with no price are
    omitted)."""
    out: dict = {}
    for book, prices in books.items():
        for s in sides:
            p = prices.get(s)
            if p is None:
                continue
            cur = out.get(s)
            if cur is None or p > cur["price"]:
                out[s] = {"price": p, "book": book}
    return out


# ---------------------------------------------------------------------------
# +EV vs consensus
# ---------------------------------------------------------------------------

def positive_ev_bets(books: dict, sides: list[str], method: str = "power",
                     min_books: int = 2) -> list[dict]:
    """For each side, the best available price and its EV against the consensus
    fair probability built from the *other* books (the priced book is excluded
    so it isn't graded against itself).

    Returns ``[{side, price, book, fair_prob, ev, n_books}]`` sorted by EV desc.
    A side is only included when at least ``min_books`` *other* books price the
    full market — otherwise there's no consensus to measure against.
    """
    best = best_prices(books, sides)
    out = []
    for side, info in best.items():
        cons = market_consensus(books, sides, method=method,
                                exclude_book=info["book"])
        if cons is None or cons["n_books"] < min_books:
            continue
        fair = cons["fair"].get(side)
        if fair is None:
            continue
        ev = odds.expected_value(fair, info["price"])
        out.append({"side": side, "price": info["price"], "book": info["book"],
                    "fair_prob": fair, "ev": ev, "n_books": cons["n_books"]})
    return sorted(out, key=lambda r: r["ev"], reverse=True)


# ---------------------------------------------------------------------------
# Arbitrage / low-hold across the best prices
# ---------------------------------------------------------------------------

def arbitrage_bet(books: dict, sides: list[str], total: float = 100.0) -> dict | None:
    """A guaranteed-profit play across the best price on each side, or ``None``.
    Returns the arbitrage math plus which book to hit for each side."""
    best = best_prices(books, sides)
    if not all(s in best for s in sides):
        return None
    arb = calculators.arbitrage([best[s]["price"] for s in sides], total=total)
    if not arb:
        return None
    legs = [{"side": s, "price": best[s]["price"], "book": best[s]["book"],
             "stake": st} for s, st in zip(sides, arb["stakes"])]
    return {"legs": legs, "profit": arb["profit"],
            "profit_pct": arb["profit_pct"], "total": arb["total"]}


def hold(books: dict, sides: list[str]) -> dict | None:
    """Combined book margin (overround) of the best price on each side, plus the
    books. A *low* hold (near zero, or negative = arbitrage) means the market is
    soft. Returns ``{"hold": pct, "books": {side: book}}`` or ``None``."""
    best = best_prices(books, sides)
    if not all(s in best for s in sides):
        return None
    h = calculators.hold(*[best[s]["price"] for s in sides])
    return {"hold": h, "books": {s: best[s]["book"] for s in sides},
            "prices": {s: best[s]["price"] for s in sides}}


# ---------------------------------------------------------------------------
# Middles (totals / spreads with a line gap)
# ---------------------------------------------------------------------------

def find_middles(offers: list[dict]) -> list[dict]:
    """Find middle opportunities in a list of total/spread offers.

    ``offers`` is ``[{"side": "over"|"under", "line": float, "price": american,
    "book": book}]``. A middle exists when you can take the **over** at a lower
    line than the **under** — any result strictly inside ``(over_line,
    under_line)`` wins both bets. Returns the best price per (side, line),
    paired into middles, sorted by window width desc:
    ``[{low, high, width, over, under, breakeven}]``.
    """
    # best price per (side, line)
    best: dict = {}
    for o in offers:
        side = str(o.get("side", "")).lower()
        line, price, book = o.get("line"), o.get("price"), o.get("book")
        if side not in ("over", "under") or line is None or price is None:
            continue
        key = (side, float(line))
        if key not in best or price > best[key]["price"]:
            best[key] = {"side": side, "line": float(line), "price": price,
                         "book": book}
    overs = [v for (s, _), v in best.items() if s == "over"]
    unders = [v for (s, _), v in best.items() if s == "under"]
    out = []
    for o in overs:
        for u in unders:
            if o["line"] < u["line"]:  # gap to middle
                out.append({
                    "low": o["line"], "high": u["line"],
                    "width": round(u["line"] - o["line"], 2),
                    "over": o, "under": u,
                    "breakeven": _middle_breakeven(o["price"], u["price"])})
    return sorted(out, key=lambda r: r["width"], reverse=True)


def _middle_breakeven(over_american: float, under_american: float) -> float:
    """Hit rate the middle must clear to break even, staking one unit per side.
    Hit (lands in window) wins both; a miss wins one leg and loses the other.
    Averaging *American* odds is meaningless (−110 and +110 average to 0), so we
    work in decimals: with do, du the decimal odds, breakeven =
    (4 − (do+du)) / (do+du), clamped at 0 (≤0 means it can't lose money)."""
    do = odds.american_to_decimal(over_american)
    du = odds.american_to_decimal(under_american)
    s = do + du
    return round(max(0.0, (4 - s) / s), 4) if s else 0.0


# ---------------------------------------------------------------------------
# Snapshot-store adapter: captured multi-book odds -> books dicts
# ---------------------------------------------------------------------------

def slate_books(sport: str, date: str, snap_dir=None) -> dict:
    """Build per-game, per-market ``{book: {side: price}}`` views from the latest
    multi-book capture in the day's snapshot log (the same source line-shopping
    uses). Returns ``{frozenset({norm_home, norm_away}): {"moneyline": {...},
    "total": {...}, "_lines": {book: total_line}, "_offers": [...]}}``.

    ``moneyline`` sides are normalized team names; ``total`` sides are
    ``over``/``under``. ``_offers`` carries every total offer (book, side, line,
    price) for the middle scanner.
    """
    from collections import defaultdict as _dd

    from .clv import _load_rows
    from .names import normalize

    rows = [r for r in _load_rows(sport, date, snap_dir)
            if r.get("kind") == "game" and r.get("source") == "oddsapi"]
    if not rows:
        return {}
    groups: dict = _dd(list)
    for r in rows:
        groups[r.get("event_id")].append(r)

    out: dict = {}
    for ers in groups.values():
        last = max((r.get("captured_at") or "") for r in ers)
        ers = [r for r in ers if (r.get("captured_at") or "") == last]
        teams = {normalize(r["participant"]) for r in ers
                 if r.get("market") == "moneyline" and r.get("participant")}
        if len(teams) != 2:
            continue
        ml: dict = _dd(dict)
        tot: dict = _dd(dict)
        offers: list = []
        for r in ers:
            book = r.get("book_id")
            price = r.get("odds")
            if price is None or book is None:
                continue
            if r.get("market") == "moneyline" and r.get("participant"):
                ml[book][normalize(r["participant"])] = price
            elif r.get("market") == "total":
                side = str(r.get("selection", "")).lower()
                key = "over" if "over" in side else "under" if "under" in side else None
                if key:
                    tot[book][key] = price
                    offers.append({"side": key, "line": r.get("line"),
                                   "price": price, "book": book})
        out[frozenset(teams)] = {"moneyline": dict(ml), "total": dict(tot),
                                 "_offers": offers}
    return out


def scan_slate(sport: str, date: str, min_ev: float = 0.0,
               snap_dir=None) -> dict:
    """Run the full edge scan over a slate. Returns
    ``{"plus_ev": [...], "arbs": [...], "middles": [...], "low_holds": [...]}``,
    each entry tagged with the matchup. Empty lists when there's no multi-book
    capture for the slate (e.g. before the Odds API is wired up)."""
    games = slate_books(sport, date, snap_dir)
    plus_ev, arbs, middles, low_holds = [], [], [], []
    for teams, mkts in games.items():
        matchup = " vs ".join(sorted(teams))
        ml = mkts.get("moneyline") or {}
        sides = sorted({s for b in ml.values() for s in b})
        if len(sides) == 2:
            for bet in positive_ev_bets(ml, sides):
                if bet["ev"] >= min_ev:
                    plus_ev.append({"market": "Moneyline", "game": matchup, **bet})
            arb = arbitrage_bet(ml, sides)
            if arb:
                arbs.append({"market": "Moneyline", "game": matchup, **arb})
            h = hold(ml, sides)
            if h and h["hold"] < 0.01:
                low_holds.append({"market": "Moneyline", "game": matchup, **h})
        tot = mkts.get("total") or {}
        if all("over" in b and "under" in b for b in tot.values()) and tot:
            for bet in positive_ev_bets(tot, ["over", "under"]):
                if bet["ev"] >= min_ev:
                    plus_ev.append({"market": "Total", "game": matchup, **bet})
        for m in find_middles(mkts.get("_offers") or []):
            middles.append({"market": "Total", "game": matchup, **m})
    plus_ev.sort(key=lambda r: r["ev"], reverse=True)
    return {"plus_ev": plus_ev, "arbs": arbs, "middles": middles,
            "low_holds": low_holds}
