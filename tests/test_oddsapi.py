"""The Odds API client: parsing, best-price line shopping, snapshot mapping,
and the credit-floor guard. No network — payloads are mocked."""

from onesource import clv, config
from onesource.clients import oddsapi
from onesource.names import normalize


def _events():
    return [{
        "id": "abc", "home_team": "Boston Red Sox", "away_team": "New York Yankees",
        "commence_time": "2026-06-12T23:05:00Z",
        "bookmakers": [
            {"key": "draftkings", "markets": [
                {"key": "h2h", "outcomes": [
                    {"name": "Boston Red Sox", "price": -110},
                    {"name": "New York Yankees", "price": -110}]},
                {"key": "totals", "outcomes": [
                    {"name": "Over", "price": -130, "point": 8.5},
                    {"name": "Under", "price": +110, "point": 8.5}]}]},
            {"key": "fanduel", "markets": [
                {"key": "h2h", "outcomes": [
                    {"name": "Boston Red Sox", "price": -105},  # better home price
                    {"name": "New York Yankees", "price": -115}]}]},
        ]}]


def test_normalize_and_best_prices():
    rows = oddsapi.normalize(_events())
    # 2 books: dk has h2h(2)+totals(2)=4, fd has h2h(2)=2 -> 6 rows
    assert len(rows) == 6
    best = oddsapi.best_prices(rows)
    # best home moneyline is fanduel's -105 (vs draftkings -110)
    home = best[("abc", "moneyline", "Boston Red Sox", None)]
    assert home["price"] == -105 and home["book"] == "fanduel"


def test_snapshot_rows_match_clv_schema():
    rows = oddsapi.snapshot_rows(_events(), "MLB", "2026-06-12", "2026-06-12T22:00:00Z")
    assert all(r["source"] == "oddsapi" and r["kind"] == "game" for r in rows)
    assert all(str(r["event_id"]).startswith("oa:") for r in rows)
    ml = [r for r in rows if r["market"] == "moneyline"]
    assert {r["participant"] for r in ml} == {"Boston Red Sox", "New York Yankees"}
    tot = [r for r in rows if r["market"] == "total"]
    assert {r["selection"] for r in tot} == {"Over", "Under"}


def test_clv_merges_oddsapi_into_consensus(tmp_path, monkeypatch):
    import json
    rows = oddsapi.snapshot_rows(_events(), "MLB", "2026-06-12",
                                 "2026-06-12T22:00:00Z")
    d = tmp_path / "mlb"
    d.mkdir(parents=True)
    with (d / "2026-06-12.jsonl").open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    closes = clv.closing_lines("MLB", "2026-06-12", snap_dir=tmp_path)
    key = frozenset({normalize("Boston Red Sox"), normalize("New York Yankees")})
    assert key in closes
    # consensus de-vigs to roughly even moneyline
    assert abs(closes[key]["moneyline"][normalize("Boston Red Sox")] - 0.5) < 0.03
    assert closes[key]["total"]["over"] > 0.5  # over priced shorter


def test_game_odds_no_key_returns_empty(monkeypatch):
    monkeypatch.setattr(config, "THE_ODDS_API_KEY", lambda: None)
    assert oddsapi.game_odds("MLB") == []


def test_credit_floor_blocks_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "THE_ODDS_API_KEY", lambda: "fake-key")
    monkeypatch.setattr(oddsapi, "_BUDGET_FILE", tmp_path / "budget.json")
    (tmp_path / "budget.json").write_text('{"remaining": 50}')
    monkeypatch.setattr(config, "ODDS_API_MIN_CREDITS", 1000)

    called = {"n": 0}

    def _boom(*a, **k):
        called["n"] += 1
        raise AssertionError("should not fetch below the credit floor")

    monkeypatch.setattr(oddsapi, "_fetch", _boom)
    assert oddsapi.game_odds("MLB") == []
    assert called["n"] == 0
