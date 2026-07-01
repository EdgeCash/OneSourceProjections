"""Score-projection hero + projected-range helpers on the matchup card."""
import app.ui as ui


def test_invert_survival_interpolates():
    op = {"7.5": 0.8, "8.5": 0.5, "9.5": 0.2}
    # median: where S=0.5 -> exactly 8.5
    assert abs(ui._invert_survival(op, 0.5) - 8.5) < 1e-9
    # p75 (S=0.25) sits between 8.5 and 9.5
    p75 = ui._invert_survival(op, 0.25)
    assert 8.5 < p75 <= 9.5


def test_total_range_from_over_probs_mlb():
    g = {"over_probs": {"6.5": 0.9, "8.5": 0.5, "10.5": 0.1}, "proj_total": 8.5}
    p25, p50, p75 = ui._total_range("MLB", g)
    assert p25 < p50 < p75


def test_total_range_normal_iqr_wnba():
    # no over_probs -> normal IQR from sigma_total
    g = {"proj_total": 165.0}
    r = ui._total_range("WNBA", g)
    assert r is not None
    p25, p50, p75 = r
    assert p25 < 165.0 < p75 and abs(p50 - 165.0) < 1e-9


def test_hero_builds_and_is_safe():
    g = {"away_team": "A", "home_team": "B", "away_exp_runs": 4.7,
         "home_exp_runs": 5.0, "proj_total": 9.7, "home_win_prob": 0.55,
         "total_line": 8.5, "over_probs": {"7.5": 0.7, "9.5": 0.3}}
    h = ui._projection_hero("MLB", g)
    assert "Score projection" in h and "Total" in h
    # never raises on empty / partial
    assert ui._projection_hero("MLB", {})
    assert ui._projection_hero("WNBA", {"home_exp": 88.0, "away_exp": 85.0})


def test_hero_flags_over_when_market_below_range():
    g = {"away_team": "A", "home_team": "B", "proj_total": 10.0,
         "over_probs": {"7.0": 0.95, "8.0": 0.85, "9.0": 0.7, "10.0": 0.5,
                        "11.0": 0.3, "12.0": 0.15}, "total_line": 6.5}
    assert "lean OVER" in ui._projection_hero("MLB", g)
