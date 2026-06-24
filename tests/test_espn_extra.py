"""Offline tests for the universal Sharp Sheet ESPN parser (fixture, no network)."""

from __future__ import annotations

from project547.clients import espn_extra


def _fixture():
    """A realistic slice of an ESPN soccer scoreboard response."""
    def competitor(name, abbr, home, score, rec, form, rank, stats):
        return {
            "homeAway": "home" if home else "away", "score": score,
            "team": {"displayName": name, "abbreviation": abbr,
                     "logo": f"https://a.espncdn.com/i/teamlogos/{abbr}.png",
                     "color": "ff0000"},
            "records": [{"summary": rec}], "form": form,
            "curatedRank": {"current": rank},
            "statistics": [{"label": k, "displayValue": v} for k, v in stats],
        }
    return {"events": [{
        "id": "9001", "name": "Inter Miami at LA Galaxy", "date": "2026-06-24T23:00Z",
        "status": {"type": {"state": "pre", "shortDetail": "7:00 PM ET"}},
        "competitions": [{
            "venue": {"fullName": "Dignity Health Sports Park"},
            "neutralSite": False,
            "odds": [{"details": "LA -110", "overUnder": 3.5,
                      "provider": {"name": "ESPN BET"}}],
            "competitors": [
                competitor("LA Galaxy", "LA", True, "0", "8-6-4", "WWDLW", 3,
                           [("Goals/G", "1.7"), ("Poss%", "55.2")]),
                competitor("Inter Miami", "MIA", False, "0", "10-4-3", "WWWDW", 1,
                           [("Goals/G", "2.1"), ("Poss%", "58.9")]),
            ],
        }],
    }]}


def test_parse_sheets_extracts_teams_logos_records_stats():
    games = espn_extra._parse_sheets(_fixture(), "MLS")
    assert len(games) == 1
    g = games[0]
    assert g["league"] == "MLS"
    assert g["home"]["name"] == "LA Galaxy" and g["away"]["name"] == "Inter Miami"
    assert g["home"]["logo"].endswith("LA.png")
    assert g["away"]["record"] == "10-4-3"
    assert g["away"]["form"] == "WWWDW"
    assert g["home"]["rank"] == 3
    assert g["venue"] == "Dignity Health Sports Park"
    assert g["state"] == "pre"
    # season stats aligned for a home-vs-away comparison
    home_stats = {s["label"]: s["value"] for s in g["home"]["stats"]}
    assert home_stats["Goals/G"] == "1.7"
    assert g["odds"]["over_under"] == 3.5
    assert g["odds"]["provider"] == "ESPN BET"


def test_parse_sheets_skips_incomplete_events():
    bad = {"events": [{"id": "x", "competitions": [{"competitors": [
        {"homeAway": "home", "team": {"displayName": "Solo"}}]}]}]}
    assert espn_extra._parse_sheets(bad, "MLS") == []


def test_registry_groups_and_paths():
    groups = espn_extra.leagues_by_group()
    assert "Soccer" in groups
    assert any(k == "mls" for k, _name in groups["Soccer"])
    # every registry entry has a sport/league path
    assert all("/" in path for path, _n, _g in espn_extra.LEAGUES.values())
