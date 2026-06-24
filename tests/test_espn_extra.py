"""Offline tests for the universal ESPN parser (fixtures, no network)."""

from __future__ import annotations

from project547.clients import espn_extra


def _soccer():
    def team(name, abbr, home, rec, form, rank, stats):
        return {
            "homeAway": "home" if home else "away", "score": "0",
            "team": {"displayName": name, "abbreviation": abbr,
                     "logo": f"https://a.espncdn.com/i/teamlogos/{abbr}.png"},
            "records": [{"summary": rec}], "form": form,
            "curatedRank": {"current": rank},
            "statistics": [{"label": k, "displayValue": v} for k, v in stats],
        }
    return {"events": [{
        "id": "9001", "name": "Inter Miami at LA Galaxy", "date": "2026-06-24T23:00Z",
        "status": {"type": {"state": "pre", "shortDetail": "7:00 PM ET"}},
        "competitions": [{
            "venue": {"fullName": "Dignity Health Sports Park"},
            "odds": [{"details": "MIA -110", "overUnder": 3.5,
                      "provider": {"name": "ESPN BET"},
                      "homeTeamOdds": {"moneyLine": 165},
                      "awayTeamOdds": {"moneyLine": 150},
                      "drawOdds": 220}],
            "competitors": [
                team("LA Galaxy", "LA", True, "8-6-4", "WWDLW", 3, [("Goals/G", "1.7")]),
                team("Inter Miami", "MIA", False, "10-4-3", "WWWDW", 1, [("Goals/G", "2.1")]),
            ],
        }],
    }]}


def _tennis():
    def player(name):
        return {"athlete": {"displayName": name, "flag": {"href": f"flag/{name}.png"}},
                "score": "0"}
    return {"events": [{
        "id": "t1", "name": "Alcaraz vs Sinner",
        "status": {"type": {"state": "pre", "shortDetail": "9:00 AM"}},
        "competitions": [{"competitors": [player("Carlos Alcaraz"),
                                          player("Jannik Sinner")]}],
    }]}


def _golf():
    def p(name, pos):
        return {"athlete": {"displayName": name}, "score": pos}
    return {"events": [{
        "id": "g1", "name": "The Open Championship",
        "status": {"type": {"state": "in", "shortDetail": "Round 2"}},
        "competitions": [{"competitors": [p("Scheffler", "-9"), p("McIlroy", "-7"),
                                          p("Schauffele", "-6")]}],
    }]}


def test_team_matchup_parsed():
    g = espn_extra._parse_events(_soccer(), "MLS")[0]
    assert g["is_matchup"] and g["home"]["name"] == "LA Galaxy"
    assert g["away"]["record"] == "10-4-3" and g["away"]["form"] == "WWWDW"
    assert g["home"]["logo"].endswith("LA.png")
    assert g["odds"]["over_under"] == 3.5
    assert g["odds"]["home_ml"] == 165 and g["odds"]["away_ml"] == 150
    assert g["odds"]["draw_ml"] == 220
    assert {s["label"]: s["value"] for s in g["away"]["stats"]}["Goals/G"] == "2.1"


def test_tennis_1v1_uses_athlete():
    g = espn_extra._parse_events(_tennis(), "ATP")[0]
    assert g["is_matchup"]
    names = {g["home"]["name"], g["away"]["name"]}
    assert names == {"Carlos Alcaraz", "Jannik Sinner"}
    assert g["home"]["logo"].startswith("flag/")


def test_golf_field_is_not_matchup():
    g = espn_extra._parse_events(_golf(), "PGA")[0]
    assert not g["is_matchup"] and g["home"] is None
    assert [c["name"] for c in g["competitors"]] == ["Scheffler", "McIlroy", "Schauffele"]
    assert g["name"] == "The Open Championship"


def test_registry_broad_and_grouped():
    groups = espn_extra.leagues_by_group()
    # covers many sports beyond the modeled ones
    for grp in ("Soccer", "Tennis", "Golf", "MMA", "Motorsport", "Football"):
        assert grp in groups, grp
    assert all("/" in path for path, _n, _g in espn_extra.LEAGUES.values())
    assert len(espn_extra.LEAGUES) >= 25
