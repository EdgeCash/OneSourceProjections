"""Offline tests for the ESPN results/slate fixes: saturation splitting,
season-type filtering, the neutral-site flag, and the NHL box mapping."""

from __future__ import annotations

from datetime import date, timedelta

from project547.clients import espn


def _event(eid, day, season_type=2, completed=True, neutral=False,
           home="Home U", away="Away U", hs=21, as_=14):
    return {
        "id": str(eid),
        "date": f"{day}T18:00Z",
        "season": {"year": 2026, "type": season_type} if season_type else {},
        "status": {"type": {"completed": completed}},
        "competitions": [{
            "neutralSite": neutral,
            "competitors": [
                {"homeAway": "home", "score": str(hs),
                 "team": {"displayName": home, "abbreviation": "HOM"}},
                {"homeAway": "away", "score": str(as_),
                 "team": {"displayName": away, "abbreviation": "AWY"}},
            ],
        }],
    }


def _no_cache(monkeypatch):
    monkeypatch.setattr(espn, "cached_json", lambda key, ttl, fn: fn())


def test_parse_events_season_type_and_neutral():
    data = {"events": [_event(1, "2026-01-03", season_type=3, neutral=True),
                       _event(2, "2026-01-04")]}
    g1, g2 = espn._parse_events(data)
    assert g1["season_type"] == 3 and g1["neutral"] is True
    assert g2["season_type"] == 2 and g2["neutral"] is False
    # missing season block -> None, still parsed
    ev = _event(3, "2026-01-05", season_type=None)
    g3 = espn._parse_events({"events": [ev]})[0]
    assert g3["season_type"] is None and g3["neutral"] is False


def test_parse_scoreboard_carries_fields_unfiltered():
    data = {"events": [_event(9, "2026-08-10", season_type=1, neutral=True)]}
    g = espn._parse_scoreboard(data, "NFL")[0]
    assert g["season_type"] == 1        # preseason still displayed
    assert g["neutral"] is True


def test_results_range_drops_preseason_and_allstar(monkeypatch):
    _no_cache(monkeypatch)
    events = [_event(1, "2026-08-10", season_type=1),   # preseason: dropped
              _event(2, "2026-08-11", season_type=2),
              _event(3, "2026-08-12", season_type=3),
              _event(4, "2026-08-13", season_type=4),   # all-star: dropped
              _event(5, "2026-08-14", season_type=None)]  # unknown: kept
    monkeypatch.setattr(espn, "_get", lambda sk, params: {"events": events})
    out = espn.results_range("NFL", "2026-08-10", "2026-08-14")
    assert [g["game_id"] for g in out] == ["2", "3", "5"]
    assert all("neutral" in g and "season_type" in g for g in out)


def test_results_range_saturation_splits_window(monkeypatch):
    """A dense window that saturates ESPN's response cap must be split until
    every event is recovered — and the request limit must override any
    per-sport espn_params limit (NCAAF's slate limit is 400)."""
    _no_cache(monkeypatch)
    monkeypatch.setattr(espn, "_RANGE_LIMIT", 50)   # keep the fixture small
    d0 = date(2026, 1, 1)
    all_events = [_event(f"{d}-{i}", (d0 + timedelta(days=d)).isoformat())
                  for d in range(20) for i in range(10)]   # 200 events
    seen_limits = []

    class _Resp:
        def __init__(self, payload):
            self._p = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self._p

    def fake_get(url, params=None, timeout=None):
        seen_limits.append(int(params["limit"]))
        lo, hi = str(params["dates"]).split("-")
        lo = f"{lo[:4]}-{lo[4:6]}-{lo[6:]}"
        hi = f"{hi[:4]}-{hi[4:6]}-{hi[6:]}"
        inside = [e for e in all_events if lo <= e["date"][:10] <= hi]
        # ESPN keeps the OLDEST `limit` events and silently drops the tail
        return _Resp({"events": inside[: int(params["limit"])]})

    monkeypatch.setattr(espn.requests, "get", fake_get)
    out = espn.results_range("NCAAF", "2026-01-01", "2026-01-20")
    assert len(out) == 200                       # nothing truncated
    assert len({g["game_id"] for g in out}) == 200
    assert len(seen_limits) > 1                  # recursion actually split
    # espn_params' limit (400) must never shrink a results request
    assert all(l == 50 for l in seen_limits)


def test_results_range_unsaturated_single_request(monkeypatch):
    _no_cache(monkeypatch)
    calls = []

    def fake(sk, params):
        calls.append(params)
        return {"events": [_event(1, "2026-02-01")]}

    monkeypatch.setattr(espn, "_get", fake)
    out = espn.results_range("NBA", "2026-02-01", "2026-02-28")
    assert len(out) == 1 and len(calls) == 1
    assert calls[0]["limit"] == espn._RANGE_LIMIT


def _nhl_summary():
    def blk(name, keys, athletes):
        return {"name": name, "keys": keys,
                "athletes": [{"athlete": {"displayName": n, "id": pid,
                                          "position": {"abbreviation": pos}},
                              "stats": stats}
                             for n, pid, pos, stats in athletes]}

    skater_keys = ["blockedShots", "hits", "plusMinus", "timeOnIce", "goals",
                   "assists", "shotsTotal", "shotsMissed", "penaltyMinutes"]
    goalie_keys = ["goalsAgainst", "shotsAgainst", "saves", "savePct",
                   "timeOnIce", "penaltyMinutes"]
    car = {"team": {"displayName": "Carolina Hurricanes", "abbreviation": "CAR"},
           "statistics": [
               blk("forwards", skater_keys, [
                   ("Sebastian Aho", 3904173, "C",
                    ["0", "1", "-1", "20:40", "1", "2", "5", "4", "2"]),
                   ("Healthy Scratch", 1, "C", []),   # no stats -> skipped
               ]),
               blk("defenses", skater_keys, [
                   ("Brent Burns", 2300, "D",
                    ["1", "0", "0", "22:46", "0", "2", "8", "0", "0"]),
               ]),
               blk("goalies", goalie_keys, [
                   ("Dustin Tokarski", 5382, "G",
                    ["3", "24", "21", ".875", "57:43", "0"]),
               ]),
           ]}
    buf = {"team": {"displayName": "Buffalo Sabres", "abbreviation": "BUF"},
           "statistics": [blk("forwards", skater_keys, [
               ("Tage Thompson", 4063, "C",
                ["0", "2", "1", "18:03", "2", "0", "6", "1", "0"])])]}
    return {"boxscore": {"players": [car, buf]}}


def test_nhl_box_mapping_matches_backfill_schema(monkeypatch):
    monkeypatch.setattr(espn, "_summary", lambda sk, eid: _nhl_summary())
    rows = {r["name"]: r for r in espn.box_player_logs("NHL", "401688304")}
    assert "Healthy Scratch" not in rows
    aho = rows["Sebastian Aho"]
    assert aho["shots"] == 5 and aho["goals"] == 1 and aho["assists"] == 2
    assert aho["points"] == 3                       # goals + assists
    assert aho["blocks"] == 0 and aho["hits"] == 1 and aho["pim"] == 2
    assert aho["toi"] == 20 * 60 + 40
    assert aho["position"] == "F" and aho["player_id"] == 3904173
    # backfill convention: full team names
    assert aho["team"] == "Carolina Hurricanes"
    assert aho["opponent"] == "Buffalo Sabres"
    burns = rows["Brent Burns"]
    assert burns["position"] == "D" and burns["shots"] == 8
    tok = rows["Dustin Tokarski"]
    assert tok["position"] == "G"
    assert tok["saves"] == 21 and tok["shots_against"] == 24
    assert tok["goals_against"] == 3
    assert "shots" not in tok                       # goalie line, not a skater
    thom = rows["Tage Thompson"]
    assert thom["opponent"] == "Carolina Hurricanes"
    # the mapped columns are exactly what nhl_props / MARKET_STAT expect
    from project547.models import nhl_props
    for cfg in nhl_props.MARKETS.values():
        assert cfg["stat"] in {"goals", "assists", "points", "shots",
                               "blocks", "saves"}
        assert cfg["stat"] in (aho if cfg["role"] == "skater" else tok)
