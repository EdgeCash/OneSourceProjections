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


def test_prep_props_surfaces_def_rank():
    props = pd.DataFrame([{
        "player": "A'ja Wilson", "market": "Points", "line": 22.5,
        "over_odds": -115, "model_over_prob": 0.60, "ev_over": 0.05,
        "opp_rank": 3,
    }])
    view = ui.prep_props(props)
    assert "Def Rk" in view.columns and view.loc[0, "Def Rk"] == 3


def test_calibration_curve_and_error():
    ledger = [
        {"market": "model_winprob", "pred_home_wp": 0.80, "home_won": 1},
        {"market": "model_winprob", "pred_home_wp": 0.80, "home_won": 1},
        {"market": "model_winprob", "pred_home_wp": 0.80, "home_won": 1},
        {"market": "model_winprob", "pred_home_wp": 0.80, "home_won": 0},
        {"market": "model_winprob", "pred_home_wp": 0.30, "home_won": 0},
        {"market": "model_winprob", "pred_home_wp": 0.30, "home_won": 1},
        {"market": "model_winprob", "pred_home_wp": 0.30, "home_won": 0},
        {"market": "moneyline", "pnl": 1.0, "won": True},  # ignored
    ]
    curve = ui.calibration_curve(ledger)
    assert len(curve) == 2
    top = curve[curve["predicted"] > 0.5].iloc[0]
    assert top["n"] == 4 and abs(top["empirical"] - 0.75) < 1e-9
    low = curve[curve["predicted"] < 0.5].iloc[0]
    assert low["n"] == 3 and abs(low["empirical"] - 1 / 3) < 1e-9
    ece = ui.calibration_error(curve)
    assert abs(ece - (4 * 0.05 + 3 * abs(0.30 - 1 / 3)) / 7) < 1e-9
    assert ui.calibration_chart(curve) is not None


def test_calibration_curve_empty():
    assert ui.calibration_curve([]).empty
    assert ui.calibration_curve([{"market": "moneyline", "pnl": 1}]).empty
    assert ui.calibration_error(ui.calibration_curve([])) is None
    assert ui.calibration_chart(ui.calibration_curve([])) is None


def test_ai_brief_game():
    g = _slates()["MLB"]["games"][0]
    matchup = {
        "away_form": {"w": 12, "l": 8, "streak": "W3",
                      "last5": [{"win": True}, {"win": False}, {"win": True}]},
        "home_form": {"w": 10, "l": 10},
        "home_off_vs_away_def": [
            {"stat": "Runs", "adv": 2, "off_rank": 3, "def_rank": 27}],
    }
    md = ui.ai_brief_game("MLB", g, matchup, min_edge=0.02)
    assert md.startswith("# MLB — New York Yankees @ Boston Red Sox")
    assert "## Model read" in md
    assert "Moneyline" in md and "PLAY" in md       # +6% home ML clears 2%
    assert "confidence" in md                        # conviction annotated
    assert "12-8 (W3)" in md                         # team form line
    assert "Biggest stat mismatches" in md and "#3 offense vs #27 defense" in md
    assert "not financial advice" in md


def test_ai_brief_game_no_matchup():
    # works off the slate alone (no team-stat matchup available)
    g = _slates()["MLB"]["games"][0]
    md = ui.ai_brief_game("MLB", g)
    assert "## Model read" in md
    assert "Team form" not in md  # nothing to show without a matchup


def test_ai_brief_prop():
    p = dict(_slates()["MLB"]["props"][0],
             hr_l5=0.8, hr_l10=0.7, bp_projection=6.8,
             bp_recommended_side="over", bp_bet_rating=3, opp_rank=27)
    md = ui.ai_brief_prop("MLB", p)
    assert md.startswith("# MLB Prop — Gerrit Cole · Pitcher Ks 6.5")
    assert "New York Yankees vs Boston Red Sox" in md
    assert "Best side: **Over 6.5**" in md and "+16.0%" in md
    assert "Suggested stake: **4.0%**" in md
    assert "L5 80% · L10 70%" in md
    assert "BettingPros projects 6.8" in md and "BP lean OVER ★★★" in md
    assert "#27 defending this stat" in md


def test_ai_brief_prop_two_sided():
    p = _slates()["WNBA"]["props"][0]  # ev_over +14%, ev_under -20%
    md = ui.ai_brief_prop("WNBA", p)
    assert "Best side: **Over 22.5**" in md  # picks the positive side
    assert "+14.0%" in md


def test_ai_brief_board():
    board = ui.build_best_bets(_slates(), min_edge=0.02)
    md = ui.ai_brief_board(board, date="2026-06-13")
    assert md.startswith("# Slate edges — 2026-06-13")
    assert "| Sport | Bet | Game | Price | Model % | EV % |" in md
    assert md.count("\n|") >= 4 + 1  # header + separator + 4 edge rows
    assert "+16.0%" in md  # Cole Ks, the top edge
    assert ui.ai_brief_board(pd.DataFrame()).startswith("No model edges")


def test_lineup_status():
    confirmed = {"lineups": {"home": list(range(9)), "away": list(range(9))}}
    assert ui.lineup_status("MLB", confirmed)["state"] == "confirmed"
    pitchers = {"home_pitcher": "Cole", "away_pitcher": "Sale"}
    assert ui.lineup_status("MLB", pitchers)["state"] == "partial"
    assert ui.lineup_status("MLB", {})["state"] == "pending"
    # a card renders the badge text
    assert "Lineups confirmed" in ui.game_card_html("MLB", {
        "home_team": "Boston Red Sox", "away_team": "New York Yankees",
        "lineups": {"home": list(range(9)), "away": list(range(9))}})


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
