"""The Conviction Rate engine: rates read straight off the slate, sides
complement correctly, and bands map as specified. Pure — no Streamlit."""

from app import conviction as cv


def _game():
    return {
        "away_team": "New York Yankees", "home_team": "Boston Red Sox",
        "away_win_prob": 0.55, "home_win_prob": 0.45,
        "away_ml": -130, "home_ml": 110, "away_ml_ev": 0.06, "home_ml_ev": -0.05,
        "away_exp_runs": 4.8, "home_exp_runs": 4.3, "proj_total": 9.1,
        "total_line": 8.5, "over_odds": -105, "under_odds": -115,
        "model_over_prob": 0.57, "over_ev": 0.05, "under_ev": -0.06,
        "rl_home_line": 1.5, "rl_home_odds": -150, "rl_away_odds": 130,
        "model_home_rl": 0.46, "rl_home_ev": -0.03, "rl_away_ev": 0.04,
        "away_pitcher": "Gerrit Cole", "home_pitcher": "Brayan Bello",
    }


def _prop():
    return {"player": "A'ja Wilson", "team": "LV", "market": "Points",
            "line": 22.5, "model_over_prob": 0.60, "projection": 24.8,
            "over_odds": -115, "under_odds": -105, "ev_over": 0.14,
            "hr_l5": 0.6, "hr_l10": 0.5, "opp_rank": 7}


def test_band_thresholds():
    assert cv.band(0.70) == "Open-and-shut"
    assert cv.band(0.60) == "Strong case"
    assert cv.band(0.54) == "Circumstantial"
    assert cv.band(0.49) == "Reasonable doubt"
    assert cv.band(0.40) == "The People decline to charge"
    assert cv.band(None) == "Mistrial"


def test_charges_and_sides():
    g = _game()
    assert cv.charges(g) == ["Moneyline", "Run Line", "Total"]
    assert dict(cv.sides(g, "Moneyline")) == {
        "away": "New York Yankees", "home": "Boston Red Sox"}
    assert dict(cv.sides(g, "Total")) == {"over": "Over 8.5", "under": "Under 8.5"}
    rl = dict(cv.sides(g, "Run Line"))
    assert rl["home"] == "Boston Red Sox +1.5" and rl["away"] == "New York Yankees -1.5"


def test_game_conviction_reads_model_prob():
    g = _game()
    assert cv.game_verdict(g, "Moneyline", "away")["rate"] == 0.55
    assert cv.game_verdict(g, "Moneyline", "home")["rate"] == 0.45
    # under is the complement of model_over_prob
    assert cv.game_verdict(g, "Total", "over")["rate"] == 0.57
    assert abs(cv.game_verdict(g, "Total", "under")["rate"] - 0.43) < 1e-9
    # run line away is the complement of the home cover prob
    assert abs(cv.game_verdict(g, "Run Line", "away")["rate"] - 0.54) < 1e-9


def test_game_verdict_shape_and_evidence():
    v = cv.game_verdict(_game(), "Moneyline", "away")
    assert v["ok"] and v["caption"] == "New York Yankees ML"
    assert v["band"] == "Circumstantial"
    assert any("project" in e for e in v["evidence"])  # projected score bullet


def test_prop_conviction_and_sides():
    p = _prop()
    assert cv.prop_sides(p) == [("over", "Over 22.5"), ("under", "Under 22.5")]
    over = cv.prop_verdict(p, "over")
    assert over["rate"] == 0.60 and "Wilson" in over["caption"]
    assert abs(cv.prop_verdict(p, "under")["rate"] - 0.40) < 1e-9
    assert any("project" in e.lower() for e in over["evidence"])


def test_missing_prob_is_mistrial():
    g = {"away_team": "A", "home_team": "B"}  # no priced markets
    assert cv.charges(g) == []
    v = cv.game_verdict(g, "Moneyline", "home")
    assert not v["ok"] and v["band"] == "Mistrial"
