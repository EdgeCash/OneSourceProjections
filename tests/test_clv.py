"""CLV: de-vigging the captured closing snapshot and scoring a taken price."""

import json

from project547 import clv
from project547.names import normalize


def _write_snapshot(tmp_path, sport, date, rows):
    d = tmp_path / sport.lower()
    d.mkdir(parents=True)
    with (d / f"{date}.jsonl").open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def test_clv_pct_matches_ev_at_close():
    # taken +120 (45.5% implied); closing fair 50% -> we beat the close
    assert clv.clv_pct(+120, 0.50) > 0
    # taken -130 (56.5% implied); closing fair 50% -> worse than close
    assert clv.clv_pct(-130, 0.50) < 0
    assert clv.clv_pct(None, 0.5) is None
    assert clv.clv_pct(-110, None) is None


def test_closing_lines_devigs_latest_capture(tmp_path):
    rows = [
        # an early capture that should be ignored (older timestamp)
        {"event_id": 1, "market": "moneyline", "participant": "Boston Red Sox",
         "selection": "home", "odds": -200, "kind": "game",
         "captured_at": "2026-06-12T18:00:00+00:00"},
        # latest capture (the close)
        {"event_id": 1, "market": "moneyline", "participant": "Boston Red Sox",
         "selection": "home", "odds": -110, "kind": "game",
         "captured_at": "2026-06-12T22:00:00+00:00"},
        {"event_id": 1, "market": "moneyline", "participant": "New York Yankees",
         "selection": "away", "odds": -110, "kind": "game",
         "captured_at": "2026-06-12T22:00:00+00:00"},
        {"event_id": 1, "market": "total", "selection": "over", "line": 8.5,
         "odds": -130, "kind": "game", "captured_at": "2026-06-12T22:00:00+00:00"},
        {"event_id": 1, "market": "total", "selection": "under", "line": 8.5,
         "odds": +110, "kind": "game", "captured_at": "2026-06-12T22:00:00+00:00"},
    ]
    _write_snapshot(tmp_path, "MLB", "2026-06-12", rows)
    closes = clv.closing_lines("MLB", "2026-06-12", snap_dir=tmp_path)

    key = frozenset({normalize("Boston Red Sox"), normalize("New York Yankees")})
    assert key in closes
    ml = closes[key]["moneyline"]
    # -110/-110 de-vigs to ~50/50 (and it used the latest -110, not the -200)
    assert abs(ml[normalize("Boston Red Sox")] - 0.5) < 0.02
    tot = closes[key]["total"]
    assert tot["line"] == 8.5
    # over priced shorter than under -> over fair prob > 0.5
    assert tot["over"] > 0.5


def test_closing_lines_missing_file(tmp_path):
    assert clv.closing_lines("MLB", "2026-06-12", snap_dir=tmp_path) == {}


def _row(**kw):
    base = {"event_id": 1, "kind": "game",
            "captured_at": "2026-06-12T22:00:00+00:00"}
    base.update(kw)
    return base


def test_totals_devig_per_book_at_modal_line(tmp_path):
    """Alt-line pooling (audit #18): the over at book A's 8.5 must never be
    de-vigged against the under at book B's 9.5. The consensus comes from the
    modal line only, per book, and the line is recorded."""
    ml = [_row(market="moneyline", participant="Boston Red Sox", book_id=b,
               odds=-110) for b in ("dk", "fd", "mgm")] + \
         [_row(market="moneyline", participant="New York Yankees", book_id=b,
               odds=-110) for b in ("dk", "fd", "mgm")]
    tots = [
        # two books quote 8.5 two-sided (the modal line)
        _row(market="total", selection="over", line=8.5, book_id="dk", odds=-105),
        _row(market="total", selection="under", line=8.5, book_id="dk", odds=-115),
        _row(market="total", selection="over", line=8.5, book_id="fd", odds=-110),
        _row(market="total", selection="under", line=8.5, book_id="fd", odds=-110),
        # one book quotes 9.5: its cheap over must NOT pollute the 8.5 fair
        _row(market="total", selection="over", line=9.5, book_id="mgm", odds=+150),
        _row(market="total", selection="under", line=9.5, book_id="mgm", odds=-190),
    ]
    _write_snapshot(tmp_path, "MLB", "2026-06-12", ml + tots)
    closes = clv.closing_lines("MLB", "2026-06-12", snap_dir=tmp_path)
    key = frozenset({normalize("Boston Red Sox"), normalize("New York Yankees")})
    tot = closes[key]["total"]
    assert tot["line"] == 8.5
    # fair from dk (-105/-115 -> over slightly < .5... actually > since -105
    # is the cheaper side) and fd (50/50); the 9.5 book (over fair ~0.39)
    # would drag this way down if pooled.
    assert 0.48 < tot["over"] < 0.53
    assert abs(tot["over"] + tot["under"] - 1.0) < 1e-9


def test_moneyline_devig_within_book_not_best_of_book(tmp_path):
    """The old best-over/best-under-across-books pair understated the hold
    (sometimes to an impossible <1.0 sum). Per-book pairs avoid that: a lone
    stale outlier price without a two-sided partner contributes nothing."""
    rows = [
        _row(market="moneyline", participant="Boston Red Sox", book_id="dk", odds=-150),
        _row(market="moneyline", participant="New York Yankees", book_id="dk", odds=+130),
        # a stale one-sided +200 dog quote at another book: no pair -> ignored
        _row(market="moneyline", participant="New York Yankees", book_id="fd", odds=+200),
    ]
    _write_snapshot(tmp_path, "MLB", "2026-06-12", rows)
    closes = clv.closing_lines("MLB", "2026-06-12", snap_dir=tmp_path)
    key = frozenset({normalize("Boston Red Sox"), normalize("New York Yankees")})
    ml = closes[key]["moneyline"]
    # dk's -150/+130 pair -> home fair ~0.58; pooling the +200 (imp .333)
    # against -150 (imp .60) would have said ~0.64 with a phantom low hold
    assert 0.55 < ml[normalize("Boston Red Sox")] < 0.61


def test_spread_close_recorded_per_team_with_line(tmp_path):
    rows = [
        _row(market="moneyline", participant="Boston Red Sox", book_id="dk", odds=-150),
        _row(market="moneyline", participant="New York Yankees", book_id="dk", odds=+130),
        _row(market="spread", participant="Boston Red Sox", book_id="dk",
             line=-1.5, odds=+120),
        _row(market="spread", participant="New York Yankees", book_id="dk",
             line=1.5, odds=-140),
    ]
    _write_snapshot(tmp_path, "MLB", "2026-06-12", rows)
    closes = clv.closing_lines("MLB", "2026-06-12", snap_dir=tmp_path)
    key = frozenset({normalize("Boston Red Sox"), normalize("New York Yankees")})
    sp = closes[key]["spread"]
    bos = sp[normalize("Boston Red Sox")]
    nyy = sp[normalize("New York Yankees")]
    assert bos["line"] == -1.5 and nyy["line"] == 1.5
    assert abs(bos["prob"] + nyy["prob"] - 1.0) < 1e-3
    assert bos["prob"] < 0.5 < nyy["prob"]    # +120 side is the underdog price
