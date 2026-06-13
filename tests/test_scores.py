"""Scoreboard + box-score parsing (mocked payloads, no network)."""

from onesource import scores
from onesource.clients import espn, mlb_statsapi


def test_espn_parse_scoreboard():
    data = {"events": [{
        "id": "401", "date": "2026-06-12T23:00Z",
        "competitions": [{
            "status": {"type": {"state": "in", "shortDetail": "Q3 4:21"}},
            "competitors": [
                {"homeAway": "home", "score": "58",
                 "team": {"displayName": "Aces", "abbreviation": "LV",
                          "logo": "x.png"}, "records": [{"summary": "10-2"}]},
                {"homeAway": "away", "score": "55",
                 "team": {"displayName": "Liberty", "abbreviation": "NY"}},
            ]}]}]}
    games = espn._parse_scoreboard(data, "WNBA")
    assert len(games) == 1
    g = games[0]
    assert g["state"] == "in" and g["detail"] == "Q3 4:21"
    assert g["home"]["abbrev"] == "LV" and g["home"]["score"] == 58.0
    assert g["away"]["abbrev"] == "NY" and g["away"]["score"] == 55.0
    assert g["home"]["record"] == "10-2"


def test_espn_box_score_generic():
    summary = {"boxscore": {"players": [{
        "team": {"abbreviation": "LV"},
        "statistics": [{"labels": ["MIN", "PTS", "REB"],
                        "athletes": [
                            {"athlete": {"displayName": "A. Wilson"},
                             "stats": ["34", "27", "11"]}]}]}]}}
    monkey = {}

    def fake_summary(_s, _e):
        return summary
    orig = espn._summary
    espn._summary = fake_summary
    try:
        box = espn.box_score("WNBA", "401")
    finally:
        espn._summary = orig
    assert box["teams"][0]["columns"] == ["Player", "MIN", "PTS", "REB"]
    assert box["teams"][0]["rows"][0] == ["A. Wilson", "34", "27", "11"]


def test_mlb_scoreboard_status(monkeypatch):
    payload = {"dates": [{"games": [{
        "gamePk": 77, "gameDate": "2026-06-12T23:05Z",
        "status": {"abstractGameCode": "I", "detailedState": "In Progress"},
        "linescore": {"inningHalf": "Top", "currentInningOrdinal": "5th"},
        "teams": {
            "home": {"team": {"name": "Red Sox", "abbreviation": "BOS", "id": 111},
                     "score": 3, "leagueRecord": {"wins": 40, "losses": 25}},
            "away": {"team": {"name": "Yankees", "abbreviation": "NYY", "id": 147},
                     "score": 2, "leagueRecord": {"wins": 38, "losses": 27}}}}]}]}
    monkeypatch.setattr(mlb_statsapi, "_get", lambda *a, **k: payload)
    # bypass the disk cache
    monkeypatch.setattr(mlb_statsapi, "cached_json", lambda key, ttl, fn: fn())
    games = mlb_statsapi.scoreboard("2026-06-12")
    g = games[0]
    assert g["state"] == "in" and "5th" in g["detail"]
    assert g["home"]["abbrev"] == "BOS" and g["home"]["score"] == 3
    assert g["away"]["record"] == "38-27"


def test_ticker_text():
    g = {"away": {"abbrev": "NYY", "score": 2}, "home": {"abbrev": "BOS", "score": 3},
         "detail": "Top 5th"}
    assert scores.ticker_text(g) == "NYY 2 @ BOS 3 · Top 5th"


def test_live_scoreboard_sorts_live_first(monkeypatch):
    monkeypatch.setattr(scores, "active_sports", lambda d: ["MLB"])
    monkeypatch.setattr(scores, "_sport_scoreboard", lambda s, d: [
        {"sport": "MLB", "state": "post", "game_time": "1", "home": {}, "away": {}},
        {"sport": "MLB", "state": "in", "game_time": "2", "home": {}, "away": {}},
        {"sport": "MLB", "state": "pre", "game_time": "3", "home": {}, "away": {}}])
    states = [g["state"] for g in scores.live_scoreboard("2026-06-12")]
    assert states == ["in", "pre", "post"]
