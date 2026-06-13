import pytest

from onesource import edge


# A two-way moneyline across three books. Pinnacle-ish tight pair plus two
# softer books; one soft book hangs a +130 dog that beats the fair line.
def _ml_books():
    return {
        "pinnacle": {"yankees": -120, "redsox": +110},
        "draftkings": {"yankees": -115, "redsox": +105},
        "fanduel": {"yankees": -125, "redsox": +130},  # soft +130 dog
    }


def test_market_consensus_devigs_and_averages():
    cons = edge.market_consensus(_ml_books(), ["yankees", "redsox"])
    assert cons["n_books"] == 3
    # fair probs sum to ~1 (de-vigged) and favorite > dog
    fav, dog = cons["fair"]["yankees"], cons["fair"]["redsox"]
    assert abs((fav + dog) - 1.0) < 1e-9
    assert fav > dog
    assert 0.5 < fav < 0.6


def test_market_consensus_exclude_book():
    full = edge.market_consensus(_ml_books(), ["yankees", "redsox"])
    minus = edge.market_consensus(_ml_books(), ["yankees", "redsox"],
                                  exclude_book="fanduel")
    assert minus["n_books"] == 2
    # FanDuel hangs the dog at a longer price (+130 = lower implied prob), so
    # dropping it RAISES the consensus dog fair probability.
    assert minus["fair"]["redsox"] > full["fair"]["redsox"]


def test_market_consensus_none_when_unpriced():
    assert edge.market_consensus({"dk": {"yankees": -110}},
                                 ["yankees", "redsox"]) is None


def test_best_prices_picks_highest_per_side():
    best = edge.best_prices(_ml_books(), ["yankees", "redsox"])
    assert best["yankees"] == {"price": -115, "book": "draftkings"}
    assert best["redsox"] == {"price": 130, "book": "fanduel"}


def test_positive_ev_flags_the_soft_dog():
    bets = edge.positive_ev_bets(_ml_books(), ["yankees", "redsox"], min_books=2)
    top = bets[0]
    # the +130 redsox at FanDuel beats the consensus of the OTHER books
    assert top["side"] == "redsox"
    assert top["book"] == "fanduel"
    assert top["price"] == 130
    assert top["ev"] > 0
    # consensus that graded it excluded FanDuel -> built from 2 books
    assert top["n_books"] == 2


def test_positive_ev_needs_enough_other_books():
    two = {"dk": {"a": -110, "b": -110}, "fd": {"a": +100, "b": -120}}
    # excluding the priced book leaves only 1 other -> min_books=2 filters out
    assert edge.positive_ev_bets(two, ["a", "b"], min_books=2) == []
    assert edge.positive_ev_bets(two, ["a", "b"], min_books=1)  # non-empty


def test_arbitrage_bet_detects_and_sizes():
    # +110 / +105 on opposite sides at different books = arb
    books = {"dk": {"a": +110, "b": -120}, "fd": {"a": -120, "b": +105}}
    arb = edge.arbitrage_bet(books, ["a", "b"], total=100)
    assert arb is not None
    assert arb["profit"] > 0
    legs = {l["side"]: l for l in arb["legs"]}
    assert legs["a"]["book"] == "dk" and legs["a"]["price"] == 110
    assert legs["b"]["book"] == "fd" and legs["b"]["price"] == 105
    assert abs(sum(l["stake"] for l in arb["legs"]) - 100) < 0.05


def test_arbitrage_none_when_no_edge():
    # a tight market with no cross-book arb (both sides juiced)
    tight = {"pinnacle": {"a": -110, "b": -110}, "dk": {"a": -112, "b": -108}}
    assert edge.arbitrage_bet(tight, ["a", "b"]) is None


def test_hold_low_for_soft_market():
    h = edge.hold(_ml_books(), ["yankees", "redsox"])
    # best of each side: -115 and +130 -> small positive hold
    assert h is not None
    assert h["hold"] < 0.02
    assert h["books"]["redsox"] == "fanduel"


def test_find_middles_pairs_gap():
    offers = [
        {"side": "over", "line": 7.5, "price": -110, "book": "dk"},
        {"side": "over", "line": 8.5, "price": +100, "book": "fd"},
        {"side": "under", "line": 8.5, "price": -105, "book": "mgm"},
        {"side": "under", "line": 9.5, "price": +110, "book": "caesars"},
    ]
    mids = edge.find_middles(offers)
    # widest middle: over 7.5 / under 9.5 -> window (7.5, 9.5), width 2.0
    assert mids[0]["low"] == 7.5 and mids[0]["high"] == 9.5
    assert mids[0]["width"] == 2.0
    assert mids[0]["over"]["book"] == "dk"
    assert mids[0]["under"]["book"] == "caesars"
    # over -110 / under +110 sum to no-vig -> a "free" middle, breakeven 0
    assert mids[0]["breakeven"] == 0.0


def test_middle_breakeven_positive_when_juiced():
    # both sides -110: averaging American (=0) used to blow up; decimals give ~4.8%
    offers = [
        {"side": "over", "line": 7.5, "price": -110, "book": "dk"},
        {"side": "under", "line": 8.5, "price": -110, "book": "fd"},
    ]
    be = edge.find_middles(offers)[0]["breakeven"]
    assert abs(be - 0.0476) < 1e-3


def test_find_middles_none_when_no_gap():
    offers = [
        {"side": "over", "line": 8.5, "price": -110, "book": "dk"},
        {"side": "under", "line": 7.5, "price": -110, "book": "fd"},
    ]
    assert edge.find_middles(offers) == []
