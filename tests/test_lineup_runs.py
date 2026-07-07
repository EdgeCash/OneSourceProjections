"""Lineup-level offense engine (roadmap T2.2) — pure-function unit tests."""
from project547 import config
from project547.models import game


def test_league_woba_maps_to_league_runs():
    # a perfectly league-average lineup projects to the league run baseline
    assert game.lineup_offense_runs(game.LEAGUE_WOBA) == config.LEAGUE_RUNS_PER_GAME


def test_better_lineup_scores_more():
    lo = game.lineup_offense_runs(0.300)
    avg = game.lineup_offense_runs(game.LEAGUE_WOBA)
    hi = game.lineup_offense_runs(0.360)
    assert lo < avg < hi


def test_blend_inert_when_off():
    # LINEUP_BLEND defaults to 0 -> passing a lineup wOBA changes nothing
    assert config.LINEUP_BLEND == 0.0
    base = game.TeamInputs(name="T", runs_per_game=4.6, opp_starter_xfip=4.1)
    withlu = game.TeamInputs(name="T", runs_per_game=4.6, opp_starter_xfip=4.1,
                             lineup_woba=0.360)
    assert game.expected_runs(base, True) == game.expected_runs(withlu, True)


def test_blend_moves_toward_lineup_estimate(monkeypatch):
    monkeypatch.setattr(config, "LINEUP_BLEND", 0.5)
    # an elite-hitting lineup should raise expected runs vs the team-rate base
    weak = game.TeamInputs(name="T", runs_per_game=4.0, opp_starter_xfip=4.1)
    strong = game.TeamInputs(name="T", runs_per_game=4.0, opp_starter_xfip=4.1,
                             lineup_woba=0.360)
    assert game.expected_runs(strong, True) > game.expected_runs(weak, True)


def test_blend_lowers_runs_for_weak_lineup(monkeypatch):
    monkeypatch.setattr(config, "LINEUP_BLEND", 0.5)
    base = game.TeamInputs(name="T", runs_per_game=4.6, opp_starter_xfip=4.1)
    weak_lu = game.TeamInputs(name="T", runs_per_game=4.6, opp_starter_xfip=4.1,
                              lineup_woba=0.290)
    assert game.expected_runs(weak_lu, True) < game.expected_runs(base, True)
