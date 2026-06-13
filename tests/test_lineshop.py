"""Line shopping: best price + book per game/market/side from the snapshot."""

import json

from onesource import lineshop
from onesource.clients import oddsapi
from onesource.names import normalize


def _events():
    return [{
        "id": "g1", "home_team": "Boston Red Sox", "away_team": "New York Yankees",
        "commence_time": "2026-06-12T23:05Z",
        "bookmakers": [
            {"key": "draftkings", "markets": [
                {"key": "h2h", "outcomes": [
                    {"name": "Boston Red Sox", "price": -120},
                    {"name": "New York Yankees", "price": +100}]},
                {"key": "totals", "outcomes": [
                    {"name": "Over", "price": -115, "point": 8.5},
                    {"name": "Under", "price": -105, "point": 8.5}]}]},
            {"key": "fanduel", "markets": [
                {"key": "h2h", "outcomes": [
                    {"name": "Boston Red Sox", "price": -110},   # better
                    {"name": "New York Yankees", "price": -102}]},
                {"key": "totals", "outcomes": [
                    {"name": "Over", "price": +100, "point": 8.5}]}]},   # better over
        ]}]


def _write(tmp_path):
    rows = oddsapi.snapshot_rows(_events(), "MLB", "2026-06-12",
                                 "2026-06-12T22:00:00Z")
    d = tmp_path / "mlb"
    d.mkdir(parents=True)
    with (d / "2026-06-12.jsonl").open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def test_best_lines_picks_best_book(tmp_path):
    _write(tmp_path)
    best = lineshop.best_lines("MLB", "2026-06-12", snap_dir=tmp_path)
    key = frozenset({normalize("Boston Red Sox"), normalize("New York Yankees")})
    assert key in best
    bos = best[key]["moneyline"][normalize("Boston Red Sox")]
    assert bos["price"] == -110 and bos["book"] == "fanduel"   # best of -120/-110
    over = best[key]["total"]["over"]
    assert over["price"] == +100 and over["book"] == "fanduel"  # best of -115/+100


def test_lookup_by_market_and_side(tmp_path):
    _write(tmp_path)
    best = lineshop.best_lines("MLB", "2026-06-12", snap_dir=tmp_path)
    ml = lineshop.lookup(best, "Boston Red Sox", "New York Yankees",
                         "moneyline", "Boston Red Sox")
    assert ml["price"] == -110
    over = lineshop.lookup(best, "Boston Red Sox", "New York Yankees",
                           "total", "over")
    assert over["price"] == +100
    # unknown matchup -> None
    assert lineshop.lookup(best, "Chicago Cubs", "St. Louis Cardinals",
                           "moneyline", "Chicago Cubs") is None


def test_lookup_ignores_propless_nan_teams(tmp_path):
    _write(tmp_path)
    best = lineshop.best_lines("MLB", "2026-06-12", snap_dir=tmp_path)
    # prop rows carry NaN home/away/market — must return None, not crash in
    # normalize() (this exception aborted the whole PLAYS tab render)
    assert lineshop.lookup(best, float("nan"), float("nan"),
                           float("nan"), None) is None
    assert lineshop.lookup({}, None, None, "moneyline", "x") is None


def test_best_lines_empty_without_oddsapi_rows(tmp_path):
    (tmp_path / "mlb").mkdir(parents=True)
    assert lineshop.best_lines("MLB", "2026-06-12", snap_dir=tmp_path) == {}
