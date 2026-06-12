import pandas as pd

from app import ui


def test_fmt_american():
    assert ui.fmt_american(120) == "+120"
    assert ui.fmt_american(-110) == "-110"
    assert ui.fmt_american(-110.0) == "-110"
    assert ui.fmt_american(None) == ""
    assert ui.fmt_american(float("nan")) == ""


def test_fmt_time_et():
    # 23:10 UTC = 7:10 PM ET in June (EDT)
    assert ui.fmt_time_et("2026-06-13T23:10:00Z") == "7:10 PM"
    assert ui.fmt_time_et(None) == ""
    assert ui.fmt_time_et("garbage") == "garbage"


def test_short_market():
    assert ui.short_market("pitcher_strikeouts") == "Pitcher Ks"
    assert ui.short_market("batter_total_bases") == "Total Bases"
    assert ui.short_market("Points") == "Points"


def _slates():
    return {
        "MLB": {
            "games": [{
                "away_team": "New York Yankees", "home_team": "Boston Red Sox",
                "game_time": "2026-06-13T23:10:00Z",
                "home_win_prob": 0.58, "away_win_prob": 0.42,
                "home_ml": -120, "home_ml_ev": 0.06,
                "away_ml": 110, "away_ml_ev": -0.05,
                "total_line": 8.5, "over_odds": -105,
                "model_over_prob": 0.55, "over_ev": 0.03,
            }],
            "props": [{
                "player": "Gerrit Cole", "team": "New York Yankees",
                "opponent": "Boston Red Sox", "market": "pitcher_strikeouts",
                "projection": 7.1, "line": 6.5, "odds": -110,
                "model_over_prob": 0.61, "ev": 0.16, "kelly": 0.04,
            }],
        },
        "WNBA": {
            "games": [],
            "props": [{
                "player": "A'ja Wilson", "team": "LV", "opponent": "IND",
                "market": "Points", "projection": 24.8, "line": 22.5,
                "over_odds": -115, "under_odds": -105,
                "model_over_prob": 0.60, "ev_over": 0.14, "ev_under": -0.20,
                "kelly": 0.04,
            }],
        },
    }


def test_build_best_bets_filters_and_sorts():
    board = ui.build_best_bets(_slates(), min_edge=0.02)
    # home ML (+6%), over (+3%), Cole Ks over (+16%), Wilson over (+14%)
    assert len(board) == 4
    assert board.iloc[0]["ev"] == 0.16  # sorted desc
    assert (board["ev"] >= 0.02).all()
    # away ML at -5% EV excluded
    assert not board["bet"].str.contains("Yankees ML").any()
    # under side with negative EV not chosen for Wilson
    wilson = board[board["bet"].str.contains("Wilson")].iloc[0]
    assert "Over" in wilson["bet"]


def test_build_best_bets_empty():
    assert ui.build_best_bets({}, 0.02).empty
    assert ui.build_best_bets({"MLB": {"games": [], "props": []}}, 0.02).empty


def test_prep_games_formats():
    games = pd.DataFrame(_slates()["MLB"]["games"])
    view = ui.prep_games(games)
    assert view.loc[0, "Home ML"] == "-120"
    assert view.loc[0, "Time"] == "7:10 PM"
    assert abs(view.loc[0, "Home Win"] - 58.0) < 1e-9   # percent scale
    assert abs(view.loc[0, "Home EV"] - 6.0) < 1e-9


def test_prep_props_formats():
    props = pd.DataFrame(_slates()["MLB"]["props"])
    view = ui.prep_props(props)
    assert view.loc[0, "Market"] == "Pitcher Ks"
    assert view.loc[0, "Odds"] == "-110"
    assert abs(view.loc[0, "EV"] - 16.0) < 1e-9


def test_cumulative_units_and_recent():
    ledger = [
        {"date": "2026-06-12", "sport": "MLB", "game": "g1", "market": "moneyline",
         "side": "home", "price": -110, "ev": 0.05, "won": True, "pnl": 0.91},
        {"date": "2026-06-13", "sport": "MLB", "game": "g2", "market": "moneyline",
         "side": "away", "price": 120, "ev": 0.04, "won": False, "pnl": -1.0},
        {"date": "2026-06-13", "sport": "MLB", "game": "g2",
         "market": "model_winprob", "side": "", "pred_home_wp": 0.5,
         "home_won": 1, "brier": 0.25},
    ]
    eq = ui.cumulative_units(ledger)
    assert list(eq["units"].round(2)) == [0.91, -0.09]
    recent = ui.recent_bets(ledger)
    assert len(recent) == 2  # winprob rows excluded
    assert recent.iloc[0]["Date"] == "2026-06-13"
    assert recent.iloc[0]["Result"].endswith("Loss")
