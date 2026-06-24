"""Offline tests for the BettingPros baseline/validator façade (no network)."""

from __future__ import annotations

from project547 import baseline, bp


def test_side_matching_is_fuzzy():
    assert bp._side_for("LA Galaxy", "Los Angeles Galaxy", "Inter Miami") == "home"
    assert bp._side_for("Inter Miami CF", "LA Galaxy", "Inter Miami") == "away"
    assert bp._side_for("Random FC", "LA Galaxy", "Inter Miami") is None


def test_consensus_two_way_medians_by_side():
    rows = [
        {"event_id": 1, "participant": "LA Galaxy", "odds": 160},
        {"event_id": 1, "participant": "LA Galaxy", "odds": 170},
        {"event_id": 1, "participant": "Inter Miami", "odds": 150},
        {"event_id": 1, "participant": "Inter Miami", "odds": 140},
        {"event_id": 2, "participant": "Other", "odds": -200},  # different event, ignored
    ]
    cons = bp._consensus_two_way(rows, 1, "LA Galaxy", "Inter Miami")
    assert cons is not None
    home_odds, away_odds = cons
    # both underdogs-ish positive prices -> a sensible de-vigged baseline
    b = baseline.market_baseline(home_odds, away_odds, source="bettingpros")
    assert b and abs(b.home_wp + b.away_wp - 1.0) < 1e-6


def test_consensus_returns_none_when_a_side_missing():
    rows = [{"event_id": 1, "participant": "LA Galaxy", "odds": 160}]
    assert bp._consensus_two_way(rows, 1, "LA Galaxy", "Inter Miami") is None


def test_find_event_by_team_tokens():
    events = [{"id": 9, "name": "Inter Miami at LA Galaxy",
               "home": "LA Galaxy", "away": "Inter Miami"},
              {"id": 10, "name": "Seattle vs Portland"}]
    ev = bp._find_event(events, "LA Galaxy", "Inter Miami")
    assert ev and ev["id"] == 9
    assert bp._find_event(events, "Dallas", "Austin") is None


def test_clean_props_filters_by_team_and_sorts_by_ev():
    flat = [
        {"participant": "Messi", "player_team": "Inter Miami", "market_id": 100,
         "bp_line": 0.5, "bp_projection": 0.8, "bp_ev": 0.12,
         "bp_recommended_side": "Over", "bp_bet_rating": 4.5},
        {"participant": "Suarez", "player_team": "Inter Miami", "market_id": 100,
         "bp_line": 0.5, "bp_projection": 0.4, "bp_ev": 0.03,
         "bp_recommended_side": "Under", "bp_bet_rating": 2.0},
        {"participant": "Other Guy", "player_team": "Some FC", "market_id": 100,
         "bp_projection": 1.0, "bp_ev": 0.99},  # filtered out by team
    ]
    rows = bp._clean_props(flat, teams=["Inter Miami", "LA Galaxy"])
    assert [r["participant"] for r in rows] == ["Messi", "Suarez"]  # EV-sorted
    assert rows[0]["bp_ev"] == 0.12


def test_validator_agreement_and_divergence():
    assert baseline.compare(0.58, 0.56)["agree"]                 # within tol
    far = baseline.compare(0.62, 0.50)
    assert not far["agree"] and "higher" in far["label"]
