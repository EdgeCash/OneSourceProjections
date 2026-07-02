"""Tests for opponent-adjusted EPA ratings and the nflverse/CFBD loaders.

All offline: the EPA ridge fit is checked on synthetic plays with a known
ground truth, and the loaders are exercised with injected transports so no
network or API key is needed.
"""

from __future__ import annotations

import math

import pytest

from project547 import epa
from project547.clients import cfbd, nflverse
from project547.models import generic
from project547.sports import SPORTS


def _round_robin_plays(off_strength, def_strength, plays_per_pair=40, noise=0.0):
    """Synthetic plays: every team plays every other (home & away). A play's EPA
    is offense_strength[posteam] - defense_strength[defteam] (+ optional noise).
    With no noise the ridge fit should recover the strengths up to a constant."""
    teams = list(off_strength)
    out = []
    for i, o in enumerate(teams):
        for d in teams:
            if o == d:
                continue
            for k in range(plays_per_pair):
                val = off_strength[o] - def_strength[d]
                if noise:
                    val += noise * math.sin(k + i)  # deterministic pseudo-noise
                out.append({"posteam": o, "defteam": d, "epa": val})
    return out


def test_ridge_recovers_relative_team_strength():
    off = {"A": 0.30, "B": 0.10, "C": -0.10, "D": -0.30}
    deff = {"A": 0.0, "B": 0.0, "C": 0.0, "D": 0.0}
    ratings = epa.team_epa_ratings(_round_robin_plays(off, deff), lam=1.0)
    # Offensive ordering must match the ground-truth ordering.
    order = sorted(ratings, key=lambda t: ratings[t].off_epa, reverse=True)
    assert order == ["A", "B", "C", "D"]
    # A's offense should rate clearly above D's.
    assert ratings["A"].off_epa > ratings["D"].off_epa + 0.3


def test_defense_is_opponent_adjusted():
    # All offenses equal; D has a dominant defense (suppresses EPA).
    off = {"A": 0.0, "B": 0.0, "C": 0.0, "D": 0.0}
    deff = {"A": 0.0, "B": 0.0, "C": 0.0, "D": 0.40}
    ratings = epa.team_epa_ratings(_round_robin_plays(off, deff), lam=1.0)
    # def_epa is EPA *allowed*: D allows the least, so lowest def_epa.
    assert min(ratings, key=lambda t: ratings[t].def_epa) == "D"


def test_ridge_shrinks_toward_zero_with_large_lambda():
    off = {"A": 0.30, "B": -0.30}
    deff = {"A": 0.0, "B": 0.0}
    weak = epa.team_epa_ratings(_round_robin_plays(off, deff), lam=1.0)
    strong = epa.team_epa_ratings(_round_robin_plays(off, deff), lam=10_000.0)
    assert abs(strong["A"].off_epa) < abs(weak["A"].off_epa)


def test_success_falls_back_to_epa_sign():
    plays = [{"posteam": "A", "defteam": "B", "epa": 1.0},
             {"posteam": "A", "defteam": "B", "epa": -1.0},
             {"posteam": "B", "defteam": "A", "epa": 0.5}]
    r = epa.team_epa_ratings(plays, lam=1.0)
    assert set(r) == {"A", "B"}
    assert all(-1.0 <= r[t].off_sr <= 1.0 for t in r)


def test_net_epa_weights_offense_more():
    t = epa.TeamEPA("X", off_epa=0.2, def_epa=0.0, off_sr=0, def_sr=0, plays=100)
    # net = (1.6*0.2 - 0) / 2.6
    assert t.net_epa == pytest.approx((1.6 * 0.2) / 2.6, abs=1e-9)


def _passer_plays(passer_strength, def_strength, n=60, cpoe=None):
    """Synthetic dropbacks: each passer faces every defense; play EPA is
    passer_strength - def_strength. Optional constant CPOE per passer."""
    out = []
    for q, qs in passer_strength.items():
        for d, ds in def_strength.items():
            for _ in range(n):
                rec = {"passer": q, "defteam": d, "epa": qs - ds}
                if cpoe is not None:
                    rec["cpoe"] = cpoe.get(q, 0.0)
                out.append(rec)
    return out


def test_passer_ratings_recover_and_opponent_adjust():
    # Q1 faces only the elite defense D1; Q2 only the leaky D2. Raw means would
    # rank Q2 above Q1, but the opponent adjustment must recover Q1 >= Q2.
    passers = {"Q1": 0.20, "Q2": 0.20, "Q3": -0.20}
    defs = {"D1": 0.30, "D2": -0.10, "D3": 0.0}
    r = epa.passer_epa_ratings(_passer_plays(passers, defs), lam=1.0)
    assert set(r) == {"Q1", "Q2", "Q3"}
    assert r["Q1"].epa > r["Q3"].epa            # true ordering recovered
    assert r["Q1"].epa == pytest.approx(r["Q2"].epa, abs=1e-6)  # equal true skill


def test_passer_cpoe_shrinks_by_dropbacks():
    passers = {"Q1": 0.1, "Q2": 0.1}
    defs = {"D1": 0.0}
    # Q1 has many dropbacks, Q2 few — same raw CPOE=10 shrinks less for Q1.
    big = _passer_plays({"Q1": 0.1}, defs, n=400, cpoe={"Q1": 10.0})
    small = _passer_plays({"Q2": 0.1}, defs, n=5, cpoe={"Q2": 10.0})
    r = epa.passer_epa_ratings(big + small, lam=1.0, cpoe_prior=200.0)
    assert r["Q1"].cpoe > r["Q2"].cpoe > 0.0


def test_passer_ratings_shrink_with_large_lambda():
    passers = {"Q1": 0.4, "Q2": -0.4}
    defs = {"D1": 0.0}
    weak = epa.passer_epa_ratings(_passer_plays(passers, defs), lam=1.0)
    strong = epa.passer_epa_ratings(_passer_plays(passers, defs), lam=10_000.0)
    assert abs(strong["Q1"].epa) < abs(weak["Q1"].epa)


def test_epa_to_points_and_margin():
    assert epa.epa_to_points(0.1, "nfl") == pytest.approx(6.4, abs=1e-6)
    home = epa.TeamEPA("H", off_epa=0.15, def_epa=-0.05, off_sr=0, def_sr=0, plays=400)
    away = epa.TeamEPA("A", off_epa=0.00, def_epa=0.05, off_sr=0, def_sr=0, plays=400)
    m = epa.expected_margin(home, away, league="nfl", hfa=2.0)
    assert m > 2.0  # home is better on both sides + HFA → positive margin


def test_empty_input():
    assert epa.team_epa_ratings([]) == {}


# --- loaders (injected transports) ------------------------------------------

def test_cfbd_client_offline():
    calls = {}

    def fake_fetch(url, params):
        calls["url"] = url
        calls["params"] = params
        return [{"team": "Georgia", "rating": 28.0}]

    c = cfbd.CFBDClient(api_key="x", fetch=fake_fetch)
    out = c.sp_ratings(2025, team="Georgia")
    assert out[0]["team"] == "Georgia"
    assert calls["url"].endswith("/ratings/sp")
    assert calls["params"]["year"] == 2025


def test_cfbd_requires_key_with_default_transport():
    c = cfbd.CFBDClient(api_key="")
    with pytest.raises(RuntimeError):
        c.team_ppa(2025)


def test_epa_blend_shifts_win_prob_and_is_consistent():
    nfl = SPORTS["NFL"]
    # A roughly even points projection (home_exp ~ away_exp).
    base = generic.GenericGameProjection(home_exp=23.0, away_exp=23.0,
                                         home_win_prob=0.5, total_mean=46.0,
                                         margin_mean=0.0)
    # EPA says home is +7. Blend at 0.5 -> margin ~+3.5 -> win prob > 0.5.
    blended = generic.with_epa_margin(base, epa_margin=7.0, sport=nfl, weight=0.5)
    assert blended.home_win_prob > 0.5
    assert blended.margin_mean == pytest.approx(3.5, abs=1e-6)
    # Cover prob at pick'em should exceed 0.5 too (consistent with the margin).
    assert blended.home_cover_prob(0.0, nfl) > 0.5


def test_epa_blend_is_noop_at_zero_weight_and_for_poisson():
    nfl, mlb = SPORTS["NFL"], SPORTS["MLB"]
    base = generic.GenericGameProjection(23.0, 20.0, 0.6, 43.0, margin_mean=3.0)
    assert generic.with_epa_margin(base, 12.0, nfl, 0.0) is base
    assert generic.with_epa_margin(base, 12.0, mlb, 0.5) is base  # poisson sport


def test_nflverse_team_ratings_from_injected_frame():
    pd = pytest.importorskip("pandas")
    rows = []
    for o, d, e in [("KC", "DEN", 0.3), ("KC", "DEN", 0.2),
                    ("DEN", "KC", -0.1), ("DEN", "KC", 0.0)]:
        rows.append({"play_type": "pass", "epa": e, "posteam": o,
                     "defteam": d, "wp": 0.5, "success": 1 if e > 0 else 0})
    # add a kneel + a garbage-time play that must be filtered out
    rows.append({"play_type": "qb_kneel", "epa": -0.5, "posteam": "KC",
                 "defteam": "DEN", "wp": 0.5, "success": 0})
    rows.append({"play_type": "pass", "epa": 5.0, "posteam": "DEN",
                 "defteam": "KC", "wp": 0.99, "success": 1})
    df = pd.DataFrame(rows)
    ratings = nflverse.team_ratings(2025, load=lambda y: df, lam=1.0)
    assert set(ratings) == {"KC", "DEN"}
    # KC's offense graded higher than DEN's on the kept plays.
    assert ratings["KC"].off_epa > ratings["DEN"].off_epa
