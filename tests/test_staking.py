"""Slate-level stake shaping: correlation haircut + exposure cap."""
from project547 import staking


def test_haircut_factor():
    assert staking.haircut_factor(1, 0.3) == 1.0        # lone leg
    assert staking.haircut_factor(3, 0.0) == 1.0        # no correlation
    assert staking.haircut_factor(2, 0.3) == 1.0 / 1.3  # pair
    assert staking.haircut_factor(3, 0.3) == 1.0 / 1.6  # triple


def test_defaults_are_identity():
    groups = ["MLB|A@B", "MLB|A@B", "NBA|C@D"]
    stakes = [0.02, 0.03, 0.01]
    # corr=0, no cap -> unchanged
    assert staking.adjust_stakes(groups, stakes, corr=0.0, max_exposure=None) == stakes


def test_correlation_haircut_only_within_group():
    groups = ["MLB|A@B", "MLB|A@B", "NBA|C@D"]   # two co-game legs, one alone
    stakes = [0.02, 0.04, 0.05]
    out = staking.adjust_stakes(groups, stakes, corr=0.3, max_exposure=None)
    assert out[0] == 0.02 / 1.3 and out[1] == 0.04 / 1.3   # halved group -> shrunk
    assert out[2] == 0.05                                   # lone leg unchanged


def test_exposure_cap_scales_proportionally():
    groups = ["a", "b", "c"]
    stakes = [0.10, 0.20, 0.10]        # total 0.40
    out = staking.adjust_stakes(groups, stakes, corr=0.0, max_exposure=0.20)
    assert abs(sum(out) - 0.20) < 1e-9
    # proportions preserved
    assert abs(out[1] - 2 * out[0]) < 1e-9


def test_exposure_cap_noop_when_under():
    groups = ["a", "b"]
    stakes = [0.05, 0.05]
    out = staking.adjust_stakes(groups, stakes, corr=0.0, max_exposure=0.20)
    assert out == stakes


def test_non_positive_stakes_pass_through_and_dont_count():
    groups = ["MLB|A@B", "MLB|A@B", "MLB|A@B"]
    stakes = [0.02, 0.0, None]         # only one positive leg in the group
    out = staking.adjust_stakes(groups, stakes, corr=0.3, max_exposure=None)
    assert out[0] == 0.02              # group size counts only the positive leg -> k=1
    assert out[1] == 0.0 and out[2] is None


def test_correlation_then_cap_compose():
    groups = ["g|1", "g|1", "g|2"]
    stakes = [0.20, 0.20, 0.20]
    out = staking.adjust_stakes(groups, stakes, corr=0.5, max_exposure=0.30)
    # group1 legs shrink by 1/(1+1*0.5)=1/1.5 -> 0.1333 each; group2 stays 0.20
    # pre-cap total = 0.1333+0.1333+0.20 = 0.4667 > 0.30 -> scaled to 0.30
    assert abs(sum(x for x in out if x) - 0.30) < 1e-9
    assert out[0] == out[1] < out[2]
