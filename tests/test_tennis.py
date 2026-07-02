"""Tennis surface-aware player-Elo model. Pure, offline."""
from __future__ import annotations

import pytest

from project547.models import tennis


def test_even_players_are_a_cointoss():
    elo = tennis.TennisElo()
    assert elo.match_prob("Player A", "Player B") == pytest.approx(0.5, abs=1e-9)


def test_winning_raises_rating_and_win_prob():
    elo = tennis.TennisElo()
    for _ in range(10):
        elo.update("Winner", "Loser", "hard")
    assert elo.rating("Winner") > elo.rating("Loser")
    assert elo.match_prob("Winner", "Loser") > 0.5
    assert elo.match_prob("Winner", "Loser") == pytest.approx(
        1 - elo.match_prob("Loser", "Winner"), abs=1e-9)


def test_surface_specialisation():
    elo = tennis.TennisElo()
    # a player who only wins on clay should be stronger on clay than on grass
    for _ in range(15):
        elo.update("ClayCourter", "Rival", "clay")
    clay = elo.match_prob("ClayCourter", "Rival", "clay")
    grass = elo.match_prob("ClayCourter", "Rival", "grass")  # unseen -> overall blend
    assert clay > grass


def test_unknown_surface_falls_back_to_overall():
    elo = tennis.TennisElo()
    for _ in range(5):
        elo.update("A", "B", "hard")
    # a bogus surface must not KeyError and should equal the overall-only prob
    assert elo.match_prob("A", "B", "moon") == pytest.approx(
        elo.match_prob("A", "B", None), abs=1e-9)


def test_seen_counts_matches():
    elo = tennis.TennisElo()
    elo.update("A", "B", "hard")
    elo.update("A", "C", "clay")
    assert elo.seen("A") == 2
    assert elo.seen("B") == 1
    assert elo.seen("Nobody") == 0


def test_blank_players_ignored():
    elo = tennis.TennisElo()
    elo.update("", "B", "hard")          # no-op, must not raise
    assert elo.seen("B") == 0
