"""NRFI model math — pure functions, no data."""
import math

from project547.models import nrfi


def test_log5_symmetry_and_base():
    # league vs league at league base -> base rate
    assert math.isclose(nrfi.log5(0.295, 0.295, 0.295), 0.295, abs_tol=1e-9)
    # better offense + worse (higher-allow) pitching -> above base
    assert nrfi.log5(0.40, 0.35, 0.295) > 0.40
    # suppressing pitcher (low allow) pulls it down
    assert nrfi.log5(0.30, 0.20, 0.295) < 0.30


def test_scale_odds_monotonic():
    assert nrfi.scale_odds(0.3, 1.0) == 0.3
    assert nrfi.scale_odds(0.3, 1.5) > 0.3      # hitter park raises scoring
    assert nrfi.scale_odds(0.3, 0.7) < 0.3


def test_regress_shrinks_to_prior():
    # thin sample pulled hard toward prior
    assert abs(nrfi.regress(0.6, 3, 0.295, 45) - 0.295) < 0.05
    # large sample stays near observed
    assert abs(nrfi.regress(0.6, 400, 0.295, 45) - 0.6) < 0.04
    assert nrfi.regress(None, 0, 0.295, 45) == 0.295


def test_yrfi_combine_and_base_rate():
    # two league-average half-innings -> YRFI near the ~50% base rate
    r = nrfi.yrfi_prob(0.295, 0.295, 0.295, 0.295, park=1.0)
    assert 0.45 <= r["p_yrfi"] <= 0.58
    assert math.isclose(r["p_yrfi"] + r["p_nrfi"], 1.0, abs_tol=1e-6)
    # strong offenses vs hittable pitching -> higher YRFI
    hi = nrfi.yrfi_prob(0.42, 0.40, 0.42, 0.40, park=1.1)
    assert hi["p_yrfi"] > r["p_yrfi"]
