import pandas as pd

from project547 import dfs


def test_hit_distribution_and_evs():
    dist = dfs.hit_distribution([0.5, 0.5])
    assert [round(x, 2) for x in dist] == [0.25, 0.5, 0.25]
    evs = dfs.slip_evs([0.6, 0.6])
    assert evs["joint"] == 0.36
    assert evs["power_ev"] == round(3.0 * 0.36 - 1, 4)
    assert evs["flex_ev"] is None  # no 2-leg flex
    e5 = dfs.slip_evs([0.6] * 5)
    assert e5["flex_ev"] is not None and -1 < e5["flex_ev"] < 10


def test_candidates_pick_better_side_and_cap():
    day = {"MLB": {"props": [
        {"player": "A", "team": "X", "market": "hits", "line": 1.5,
         "model_over_prob": 0.30},
        {"player": "B", "team": "Y", "market": "ks", "line": 5.5,
         "model_over_prob": 0.90},
        {"player": "C", "team": "Z", "market": "tb", "line": 1.5,
         "model_over_prob": None},
    ]}}
    c = dfs.candidates(day)
    assert len(c) == 2  # C dropped (no prob)
    a = c[c["player"] == "A"].iloc[0]
    assert a["side"] == "Under" and a["prob"] == 0.70
    b = c[c["player"] == "B"].iloc[0]
    assert b["side"] == "Over" and b["prob"] == dfs.PROB_CAP  # capped from .90


def test_best_slips_team_cap_and_sizes():
    rows = [{"sport": "MLB", "player": f"P{i}", "team": "T" + str(i % 2),
             "market": "m", "line": 1.5, "side": "Over",
             "prob": 0.7 - i * 0.01, "raw_prob": 0.7,
             "book_prob": 0.6, "edge": 0.05} for i in range(8)]
    slips = dfs.best_slips(pd.DataFrame(rows), max_per_team=2)
    assert [s["size"] for s in slips] == [2, 3, 4]  # only 4 legs pass team cap
    teams = [l["team"] for l in slips[-1]["legs"]]
    assert max(teams.count(t) for t in set(teams)) <= 2
    assert slips[0]["breakeven"] == dfs.per_leg_breakeven(2)


def test_per_leg_breakeven():
    assert dfs.per_leg_breakeven(2) == round(3.0 ** -0.5, 4)      # ~0.5774
    assert dfs.per_leg_breakeven(3) == round(5.0 ** (-1 / 3), 4)  # ~0.5848
    assert dfs.per_leg_breakeven(7) is None


def test_candidates_edge_vs_book():
    day = {"NBA": {"props": [
        {"player": "A", "team": "X", "market": "points", "line": 24.5,
         "model_over_prob": 0.62, "over_odds": -120, "under_odds": 100},
        {"player": "B", "team": "Y", "market": "reb", "line": 8.5,
         "model_over_prob": 0.55},  # no odds -> edge None
    ]}}
    c = dfs.candidates(day)
    a = c[c["player"] == "A"].iloc[0]
    assert a["side"] == "Over" and a["book_prob"] == 0.5217
    assert a["edge"] == round(0.62 - 0.5217, 4)
    b = c[c["player"] == "B"].iloc[0]
    assert b["edge"] is None or pd.isna(b["edge"])


def test_best_slips_min_edge_filters_no_edge_legs():
    rows = []
    for i in range(6):
        edge = 0.06 if i < 3 else 0.0   # only 3 legs beat the book
        rows.append({"sport": "NBA", "player": f"P{i}", "team": f"T{i}",
                     "market": "m", "line": 1.5, "side": "Over",
                     "prob": 0.65, "raw_prob": 0.65, "book_prob": 0.65 - edge,
                     "edge": edge})
    slips = dfs.best_slips(pd.DataFrame(rows), min_edge=0.03)
    assert [s["size"] for s in slips] == [2, 3]  # only the 3 edge legs eligible


def test_candidates_prefers_dfs_line_when_present():
    # Consensus line 6.5; PrizePicks posts a softer 5.5 -> our over prob there is
    # higher and that's the number we'd actually bet.
    day = {"MLB": {"props": [{
        "player": "Wheeler", "team": "PHI", "market": "strikeouts",
        "line": 6.5, "model_over_prob": 0.40,        # consensus prob (ignored)
        "over_odds": -110, "under_odds": -110,
        "pp_line": 5.5, "pp_model_over_prob": 0.63,
        "pp_over_odds": -119, "pp_under_odds": -119,
    }]}}
    c = dfs.candidates(day)
    assert len(c) == 1
    r = c.iloc[0]
    assert r["line_source"] == "PrizePicks"
    assert r["line"] == 5.5 and r["side"] == "Over" and r["prob"] == 0.63
    # de-vigged -119/-119 ~ 0.5 each -> edge ~ 0.13
    assert r["edge"] is not None and 0.10 < r["edge"] < 0.15


def test_candidates_picks_better_dfs_operator_per_player():
    day = {"MLB": {"props": [{
        "player": "X", "team": "T", "market": "pts", "line": 20.5,
        "model_over_prob": 0.5,
        "pp_line": 20.5, "pp_model_over_prob": 0.55,
        "pp_over_odds": -119, "pp_under_odds": -119,
        "ud_line": 18.5, "ud_model_over_prob": 0.66,   # softer UD line -> bigger edge
        "ud_over_odds": -120, "ud_under_odds": -110,
    }]}}
    c = dfs.candidates(day)
    assert len(c) == 1  # one pick per player -> the better operator
    assert c.iloc[0]["line_source"] == "Underdog" and c.iloc[0]["line"] == 18.5


def test_candidates_dfs_only_drops_consensus_props():
    day = {"MLB": {"props": [
        {"player": "A", "team": "T", "market": "m", "line": 1.5,
         "model_over_prob": 0.7, "over_odds": -110, "under_odds": -110},
        {"player": "B", "team": "T", "market": "m", "line": 2.5,
         "model_over_prob": 0.6, "pp_line": 2.5, "pp_model_over_prob": 0.6,
         "pp_over_odds": -119, "pp_under_odds": -119},
    ]}}
    c = dfs.candidates(day, dfs_only=True)
    assert list(c["player"]) == ["B"]
