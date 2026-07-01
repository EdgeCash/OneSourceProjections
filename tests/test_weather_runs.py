"""Temperature adjustment in the MLB run model (warm air -> more scoring)."""
from project547 import config
from project547.models import game


def _runs(temp):
    ti = game.TeamInputs(name="T", runs_per_game=4.6, opp_starter_xfip=4.1,
                         temp_f=temp)
    return game.expected_runs(ti, is_home=True)


def test_hot_scores_more_than_cold():
    assert _runs(95) > _runs(72) > _runs(45)


def test_none_temp_is_neutral():
    # dome / unknown temp -> no adjustment, equals the baseline temperature
    assert _runs(None) == _runs(config.TEMP_BASELINE_F)


def test_adjustment_matches_coef():
    # The multiplier applies to the pre-HFA base; HFA (+HOME_FIELD_RUNS/2) is
    # added afterward, so back it out before comparing the ratio.
    hfa = config.HOME_FIELD_RUNS / 2
    base = _runs(config.TEMP_BASELINE_F) - hfa
    hot = _runs(config.TEMP_BASELINE_F + 10) - hfa
    assert abs(hot / base - (1 + config.TEMP_COEF * 10)) < 1e-6


def test_extreme_temp_is_clamped():
    # 200F away from baseline would blow past the clamp; must be capped.
    ti = game.TeamInputs(name="T", runs_per_game=4.6, opp_starter_xfip=4.1,
                         temp_f=config.TEMP_BASELINE_F + 1000)
    base = _runs(config.TEMP_BASELINE_F)
    assert game.expected_runs(ti, True) <= base * (1 + config.TEMP_CLAMP) + 1e-9
