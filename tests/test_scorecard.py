from onesource import scorecard


def _g(date, game, model, market, won, sport="MLB"):
    return {"date": date, "sport": sport, "game": game, "market": "model_winprob",
            "pred_home_wp": model, "market_home_wp": market, "home_won": won}


def _bet(date, game, pnl, clv=None, won=True, sport="MLB"):
    return {"date": date, "sport": sport, "game": game, "market": "moneyline",
            "side": "home", "pnl": pnl, "clv": clv, "won": won}


def test_classified_games_annotates_agreement():
    rows = [_g("d1", "A@B", 0.60, 0.55, 1),   # both pick home, agree
            _g("d1", "C@D", 0.60, 0.40, 0)]   # model home, market away -> disagree
    g = scorecard.classified_games(rows)
    assert g[0]["agree"] is True and g[0]["model_pick"] == "home"
    assert g[1]["agree"] is False
    assert g[1]["model_pick"] == "home" and g[1]["market_pick"] == "away"


def test_classified_skips_rows_without_market_prob():
    rows = [{"market": "model_winprob", "pred_home_wp": 0.6, "home_won": 1},  # no market
            _g("d1", "A@B", 0.6, 0.5, 1)]
    assert len(scorecard.classified_games(rows)) == 1


def test_scorecard_disagree_bucket_shows_independent_skill():
    # On the 2 disagreement games the model is right both times and the market
    # wrong -> model brier lower, accuracy higher (independent skill).
    rows = [
        _g("d1", "A@B", 0.70, 0.40, 1),   # disagree, model(home) right
        _g("d2", "C@D", 0.30, 0.60, 0),   # disagree, model(away) right
        _g("d3", "E@F", 0.65, 0.60, 1),   # agree, both right
    ]
    sc = scorecard.scorecard(rows)
    assert sc["n_games"] == 3
    assert sc["disagree"]["n"] == 2
    assert sc["agree"]["n"] == 1
    assert sc["disagree"]["model_acc"] == 1.0
    assert sc["disagree"]["market_acc"] == 0.0
    # model better calibrated on disagreements -> positive brier_edge
    assert sc["disagree"]["brier_edge"] > 0


def test_scorecard_min_edge_reclassifies_same_side_gap():
    # same pick (home) but a 0.20 prob gap; min_edge=0.15 treats it as disagree
    rows = [_g("d1", "A@B", 0.75, 0.55, 1)]
    assert scorecard.scorecard(rows)["disagree"]["n"] == 0          # default: agree
    assert scorecard.scorecard(rows, min_edge=0.15)["disagree"]["n"] == 1


def test_bet_scorecard_splits_contrarian_vs_with_market():
    rows = [
        _g("d1", "A@B", 0.70, 0.40, 1),                 # disagree game
        _g("d2", "C@D", 0.65, 0.60, 1),                 # agree game
        _bet("d1", "A@B", pnl=0.9, clv=0.03, won=True),  # contrarian bet, won
        _bet("d2", "C@D", pnl=-1.0, clv=-0.01, won=False),  # with-market bet, lost
    ]
    bs = scorecard.bet_scorecard(rows)
    assert bs["contrarian"]["n"] == 1 and bs["contrarian"]["roi_pct"] == 90.0
    assert bs["with_market"]["n"] == 1 and bs["with_market"]["roi_pct"] == -100.0
    assert bs["contrarian"]["avg_clv_pct"] == 3.0


def test_optimal_shrink_needs_enough_games():
    rows = [_g(f"d{i}", "A@B", 0.6, 0.5, 1) for i in range(10)]
    out = scorecard.optimal_shrink(rows, min_games=30)
    assert out["ready"] is False and out["n"] == 10


def test_optimal_shrink_picks_model_when_model_is_better():
    # model is perfect (prob == outcome), market is a coin flip -> alpha ~ 0
    rows = []
    for i in range(40):
        won = i % 2
        rows.append(_g(f"d{i}", "A@B", float(won), 0.5, won))
    out = scorecard.optimal_shrink(rows, current=0.5, min_games=30)
    assert out["ready"] is True
    assert out["best_brier_alpha"] == 0.0
    assert out["model_only_brier"] < out["market_only_brier"]
    assert out["current_brier"] >= out["best_brier"]  # current 0.5 is worse


def test_optimal_shrink_picks_market_when_market_is_better():
    rows = []
    for i in range(40):
        won = i % 2
        rows.append(_g(f"d{i}", "A@B", 0.5, float(won), won))  # market perfect
    out = scorecard.optimal_shrink(rows, min_games=30)
    assert out["best_brier_alpha"] == 1.0


def test_empty_inputs():
    assert scorecard.scorecard([])["n_games"] == 0
    assert scorecard.scorecard([])["disagree"]["n"] == 0
    assert scorecard.bet_scorecard([])["contrarian"]["n"] == 0
    assert scorecard.optimal_shrink([])["ready"] is False
