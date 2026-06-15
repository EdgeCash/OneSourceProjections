"""Per-book prop extraction, incl. DFS operators (PrizePicks/Underdog), from
the BettingPros props payload (selections[].books[].lines[] nesting)."""

from onesource.clients import bettingpros as bp

# Mirrors the live props shape flatten_props already iterates: each prop has
# over/under selections, each with a per-book lines list.
RAW = [{
    "event_id": 101, "market_id": 156,
    "participant": {"name": "Luka Doncic", "player": {"team": "DAL"}},
    "selections": [
        {"selection": "over", "books": [
            {"id": 10, "name": "DraftKings", "lines": [{"line": 28.5, "cost": -115, "active": True}]},
            {"id": 24, "name": "PrizePicks", "lines": [{"line": 27.5, "cost": -119, "active": True}]},
            {"id": 25, "name": "Underdog", "lines": [{"line": 28.0, "cost": -120, "active": True}]},
        ]},
        {"selection": "under", "books": [
            {"id": 24, "name": "PrizePicks", "lines": [{"line": 27.5, "cost": -119, "active": True}]},
        ]},
    ],
}]


def test_prop_book_lines_keeps_book_identity():
    rows = bp.prop_book_lines(RAW)
    # 3 over books + 1 under book = 4 rows
    assert len(rows) == 4
    pp_over = next(r for r in rows if r["book_name"] == "prizepicks" and r["side"] == "over")
    assert pp_over["line"] == 27.5 and pp_over["participant"] == "Luka Doncic"
    assert pp_over["market_id"] == 156 and pp_over["book_id"] == 24
    dk = next(r for r in rows if r["book_name"] == "draftkings")
    assert dk["line"] == 28.5            # books carry different lines -> the edge


def test_dfs_prop_lines_filters_to_dfs_operators():
    dfs = bp.dfs_prop_lines(RAW)
    names = {r["book_name"] for r in dfs}
    assert names == {"prizepicks", "underdog"}     # DraftKings excluded
    # the DFS line (27.5) sits below the book line (28.5) -> a soft PP number
    pp = next(r for r in dfs if r["book_name"] == "prizepicks" and r["side"] == "over")
    assert pp["line"] == 27.5


def test_dfs_prop_lines_by_explicit_book_id():
    dfs = bp.dfs_prop_lines([{**RAW[0], "selections": [
        {"selection": "over", "books": [
            {"id": 99, "name": "", "lines": [{"line": 5.5, "cost": -118, "active": True}]}]}]}],
        book_ids={99})
    assert len(dfs) == 1 and dfs[0]["line"] == 5.5


def test_inactive_lines_dropped():
    raw = [{"event_id": 1, "market_id": 2, "participant": "X", "selections": [
        {"selection": "over", "books": [
            {"id": 24, "name": "PrizePicks",
             "lines": [{"line": 1.5, "cost": -119, "active": False}]}]}]}]
    assert bp.prop_book_lines(raw) == []


# /offers per-book rows (flatten_offers shape) carrying DFS books by id.
OFFER_ROWS = [
    {"event_id": 1, "market_id": 285, "market": "strikeouts",
     "participant": "Zack Wheeler", "selection": "over",
     "book_id": 37, "line": 5.5, "odds": -119, "active": True},
    {"event_id": 1, "market_id": 285, "market": "strikeouts",
     "participant": "Zack Wheeler", "selection": "under",
     "book_id": 37, "line": 5.5, "odds": -119, "active": True},
    {"event_id": 1, "market_id": 285, "market": "strikeouts",
     "participant": "Zack Wheeler", "selection": "over",
     "book_id": 36, "line": 6.0, "odds": -110, "active": True},
    {"event_id": 1, "market_id": 285, "market": "strikeouts",
     "participant": "Zack Wheeler", "selection": "over",
     "book_id": 12, "line": 6.5, "odds": 124, "active": True},   # DraftKings, ignored
]


def test_dfs_offer_lines_pivots_pp_and_ud():
    rows = bp.dfs_offer_lines(OFFER_ROWS)
    # PrizePicks (37) over+under collapse to one record; Underdog (36) one more
    assert len(rows) == 2
    pp = next(r for r in rows if r["book_id"] == bp.PRIZEPICKS_BOOK_ID)
    assert pp["book_name"] == "prizepicks"
    assert pp["over_line"] == 5.5 and pp["over_odds"] == -119
    assert pp["under_line"] == 5.5 and pp["under_odds"] == -119
    ud = next(r for r in rows if r["book_id"] == bp.UNDERDOG_BOOK_ID)
    assert ud["over_line"] == 6.0 and ud["under_line"] is None  # only over quoted
    # DraftKings (12) excluded entirely
    assert all(r["book_id"] in bp.DFS_BOOK_IDS for r in rows)


def test_dfs_offer_lines_skips_inactive_and_missing():
    rows = bp.dfs_offer_lines([
        {"event_id": 1, "market_id": 2, "participant": "X", "selection": "over",
         "book_id": 37, "line": 1.5, "odds": -119, "active": False},
        {"event_id": 1, "market_id": 2, "participant": "Y", "selection": "over",
         "book_id": 37, "line": None, "odds": -119, "active": True},
    ])
    assert rows == []
