"""Tests for opponent-adjusted EPA ratings and the nflverse/CFBD loaders.

All offline: the EPA ridge fit is checked on synthetic plays with a known
ground truth, and the loaders are exercised with injected transports so no
network or API key is needed.
"""

from __future__ import annotations

import math

import pytest

from onesource import epa
from onesource.clients import cfbd, nflverse


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
