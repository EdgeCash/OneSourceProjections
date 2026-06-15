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
