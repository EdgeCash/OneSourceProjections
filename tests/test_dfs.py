import pandas as pd

from onesource import dfs


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
             "prob": 0.7 - i * 0.01, "raw_prob": 0.7} for i in range(8)]
    slips = dfs.best_slips(pd.DataFrame(rows), max_per_team=2)
    assert [s["size"] for s in slips] == [2, 3, 4]  # only 4 legs pass team cap
    teams = [l["team"] for l in slips[-1]["legs"]]
    assert max(teams.count(t) for t in set(teams)) <= 2
