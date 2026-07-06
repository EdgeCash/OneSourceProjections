"""Smoke tests for the 360Five Terminal card adapter — build_g maps real data
and never invents; terminal_card_html injects G over the demo and never raises."""

from app import terminal_card as tc


def _game():
    return {
        "away_team": "New York Mets", "home_team": "Atlanta Braves",
        "game_time": "2026-07-05T18:15:00Z",
        "away_ml": -115, "home_ml": 100, "total_line": 8.5,
        "home_win_prob": 0.55, "away_win_prob": 0.45,
        "home_ml_ev": 0.03, "away_ml_ev": -0.02,
        "over_ev": -0.03, "model_over_prob": 0.49,
        "away_pitcher": "N. McLean", "home_pitcher": "M. Perez",
        "lineups": {"away": ["F. Lindor", "J. Soto"], "home": ["R. Acuna", "M. Olson"]},
        "weather": {"temp_f": 86, "wind_mph": 4, "wind_dir": "out", "precip_pct": 2},
    }


def _matchup():
    def row(stat, o, d, orank, drank, adv):
        r = {"stat": stat, "off_rank": orank, "def_rank": drank, "adv": adv,
             "off_situ_label": "AWAY", "def_situ_label": "HOME",
             "off_situ": o, "def_situ": d}
        for w in ("season", "l30", "l20", "l15", "l10", "l5"):
            r[f"off_{w}"], r[f"def_{w}"] = o, d
        return r
    rows = [row("Runs/G", 4.1, 4.6, 20, 8, 1), row("Hits/G", 8.0, 8.5, 15, 12, 0)]
    form = {"w": 40, "l": 45, "streak": "L2",
            "last5": [{"win": False}, {"win": True}, {"win": False}]}
    return {"window_label": "L5", "n_teams": 30,
            "home_form": form, "away_form": form,
            "home_rest": 1, "away_rest": 2,
            "home_power_rank": 15, "away_power_rank": 25,
            "home_sos_rank": 7, "away_sos_rank": 22,
            "away_off_vs_home_def": rows, "home_off_vs_away_def": rows}


def _props():
    return [{"player": "F. Lindor", "team": "New York Mets", "opponent": "Atlanta Braves",
             "market": "batter_total_bases", "line": 1.5, "odds": -120,
             "model_over_prob": 0.55, "ev": 0.04, "n": 40}]


def test_build_g_shapes_all_keys():
    g = tc.build_g("MLB", _game(), _matchup(), _props())
    for k in ("league", "grade", "away", "home", "odds", "meta", "starters",
              "sections", "conf", "lineups", "receipts", "share", "props"):
        assert k in g, k
    assert g["league"] == "MLB"
    assert g["away"]["name"] == "New York Mets"
    assert g["odds"]["ou"] == "8.5"
    # MLB gets batting / pitching / bullpen sections from the two perspectives
    labels = [s["label"] for s in g["sections"]]
    assert any("Batting" in x for x in labels) and any("Bullpen" in x for x in labels)
    assert len(g["sections"][0]["rows"][0]) == 4       # reference row shape
    assert g["starters"]["away"]["name"] == "N. McLean"
    assert g["conf"]["ml"]["c"] in ("play", "lean", "pass")


def test_build_g_data_gap_on_empty_matchup():
    g = tc.build_g("MLB", _game(), {}, [])
    # no matchup -> no matrix sections at all (never fabricated numbers)
    assert g["sections"] == []
    # meta still renders the fields it can, DATA GAP the rest
    assert any(m["k"] == "Power Rank" for m in g["meta"])


def test_non_mlb_uses_offense_defense_sections():
    g = tc.build_g("WNBA", {**_game(), "away_team": "Aces", "home_team": "Liberty",
                            "away_pitcher": None, "home_pitcher": None}, _matchup(), [])
    labels = [s["label"] for s in g["sections"]]
    assert any("Offense" in x for x in labels)
    assert g["starters"] is None            # no pitcher/goalie/QB for WNBA


def test_tennis_match_variant():
    tg = {"player1": "A. Zverev", "player2": "T. Fritz", "match_time": "2026-07-05T12:00:00Z",
          "player1_win_prob": 0.61, "player2_win_prob": 0.39,
          "p1_price": -135, "p2_price": 115, "p1_ev": 0.03, "p2_ev": -0.03,
          "p1_matches": 40, "p2_matches": 38, "tournament": "Wimbledon", "surface": "grass"}
    g = tc.build_g("ATP", tg)
    assert g["away"]["name"] == "A. Zverev" and g["home"]["name"] == "T. Fritz"
    assert g["sections"] == [] and g["starters"] is None    # no matrix / starter
    assert g["conf"]["ml"]["v"] != "—"                      # match-winner conviction
    assert any(m["k"] == "Surface" for m in g["meta"])
    html = tc.terminal_card_html("ATP", tg)
    assert "A. Zverev" in html and "Wimbledon" in html


def test_lineup_joins_props_and_gaps():
    g = tc.build_g("MLB", _game(), _matchup(), _props())
    away = g["lineups"]["away"]["order"]
    assert away[0]["n"].endswith("F. Lindor")
    # Lindor has a prop -> a tag; the other hitter has none -> DATA GAP
    assert away[0]["tag"] != "—"
    assert away[1]["tag"] == "—"
    assert any("Lindor" in k for k in g["props"])


def test_terminal_card_html_injects_and_never_raises():
    html = tc.terminal_card_html("MLB", _game(), _matchup(), _props())
    assert "Object.assign(G," in html.replace(" ", "")
    assert "New York Mets" in html and "Atlanta Braves" in html
    assert "renderResearch();" in html
    # bad input degrades to the demo template rather than raising
    assert isinstance(tc.terminal_card_html("MLB", None, None, None), str)
