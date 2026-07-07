"""mlb_statsapi ingest-path integrity (audit #10): box_player_logs must use
its own cache key (not box_score's 45s snapshot key) and must refuse to
ingest a box until the game's status is Final."""
from project547.clients import mlb_statsapi as api

_BOX = {
    "teams": {
        "home": {"team": {"abbreviation": "BOS"}, "players": {
            "ID1": {"person": {"fullName": "Test Slugger", "id": 1},
                    "position": {"abbreviation": "1B"},
                    "stats": {"batting": {"hits": 2, "atBats": 4}}}}},
        "away": {"team": {"abbreviation": "NYY"}, "players": {}},
    }
}


def _fake_cache(store):
    def cached_json(key, ttl, fetch):
        store.setdefault("keys", []).append(key)
        return store["payloads"][key.split(":")[1]]
    return cached_json


def test_box_player_logs_uses_own_cache_key(monkeypatch):
    store = {"payloads": {"boxlogs": _BOX,
                          "gamestatus": {"dates": [{"games": [
                              {"gamePk": 777, "status": {"codedGameState": "F"}}]}]}}}
    monkeypatch.setattr(api, "cached_json", _fake_cache(store))
    rows = api.box_player_logs(777)
    assert rows and rows[0]["name"] == "Test Slugger"
    keys = store["keys"]
    assert "statsapi:boxlogs:777" in keys          # ingest-only key
    assert "statsapi:box:777" not in keys          # never the shared live key


def test_box_player_logs_refuses_non_final(monkeypatch):
    store = {"payloads": {"boxlogs": _BOX,
                          "gamestatus": {"dates": [{"games": [
                              {"gamePk": 777, "status": {"codedGameState": "I"}}]}]}}}
    monkeypatch.setattr(api, "cached_json", _fake_cache(store))
    assert api.box_player_logs(777) == []
    # the (potentially partial) box is never fetched/cached for ingest
    assert all(not k.startswith("statsapi:boxlogs") for k in store["keys"])


def test_box_player_logs_refuses_unknown_status(monkeypatch):
    monkeypatch.setattr(api, "game_status", lambda pk: None)
    monkeypatch.setattr(api, "cached_json",
                        lambda key, ttl, fetch: (_ for _ in ()).throw(
                            AssertionError("must not fetch the box")))
    assert api.box_player_logs(777) == []


def test_game_status_parses_schedule(monkeypatch):
    payload = {"dates": [{"games": [
        {"gamePk": 1, "status": {"codedGameState": "I"}},
        {"gamePk": 2, "status": {"codedGameState": "F"}}]}]}
    monkeypatch.setattr(api, "cached_json", lambda key, ttl, fetch: payload)
    assert api.game_status(2) == "F"
    assert api.game_status(1) == "I"
    assert api.game_status(3) is None
