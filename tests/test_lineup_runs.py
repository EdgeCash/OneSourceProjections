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


def test_blend_inert_when_off(monkeypatch):
    # with the blend forced off, passing a lineup wOBA changes nothing
    monkeypatch.setattr(config, "LINEUP_BLEND", 0.0)
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


# ---------------------------------------------------------------------------
# Season-aware league-wOBA anchor (audit #12)
# ---------------------------------------------------------------------------

def test_league_woba_anchor_moves_wraa():
    # a .310 lineup in a .310 league is league-average, not below it
    anchored = game.lineup_offense_runs(0.310, league_woba=0.310)
    assert anchored == config.LEAGUE_RUNS_PER_GAME
    stale = game.lineup_offense_runs(0.310)          # 0.318 constant anchor
    assert stale < anchored
    # default (None) keeps the proxy path's 0.318 construction anchor
    assert game.lineup_offense_runs(game.LEAGUE_WOBA) == config.LEAGUE_RUNS_PER_GAME


def test_team_inputs_league_woba_feeds_expected_runs(monkeypatch):
    monkeypatch.setattr(config, "LINEUP_BLEND", 0.5)
    lo = game.TeamInputs(name="T", runs_per_game=4.5, opp_starter_xfip=4.1,
                         lineup_woba=0.310, league_woba=0.310)
    hi = game.TeamInputs(name="T", runs_per_game=4.5, opp_starter_xfip=4.1,
                         lineup_woba=0.310)          # default 0.318 anchor
    assert game.expected_runs(lo, True) > game.expected_runs(hi, True)


def test_batwoba_league_woba_pa_weighted(monkeypatch):
    from project547 import batwoba, history
    xstats = {"batting": {
        "1": {"woba": 0.400, "pa": 300},
        "2": {"woba": 0.300, "pa": 100},
        "3": {"woba": None, "pa": 500},      # no wOBA -> excluded
        "4": {"woba": 0.350, "pa": 0},       # no PA -> excluded
    }}
    monkeypatch.setattr(history, "statcast_xstats",
                        lambda season: xstats if season == 1999 else {})
    batwoba.league_woba.cache_clear()
    try:
        # (0.400*300 + 0.300*100) / 400 = 0.375
        assert batwoba.league_woba(1999) == 0.375
        # missing season file -> None (pipeline falls back to the default)
        assert batwoba.league_woba(1998) is None
    finally:
        batwoba.league_woba.cache_clear()
