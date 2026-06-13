"""CLV: de-vigging the captured closing snapshot and scoring a taken price."""

import json

from onesource import clv
from onesource.names import normalize


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
