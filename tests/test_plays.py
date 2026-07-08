"""First-qualify DFS play logging, closing-line refresh, and grading."""

import project547.plays as plays


def _blob(pp_line=5.5, pp_prob=0.63):
    return {"MLB": {"props": [
        # qualifies: PrizePicks line priced, big edge
        {"player": "Zack Wheeler", "team": "PHI", "market": "strikeouts",
         "line": 6.5, "model_over_prob": 0.40,
         "pp_line": pp_line, "pp_model_over_prob": pp_prob,
         "pp_over_odds": -119, "pp_under_odds": -119},
        # below the edge bar -> not logged
        {"player": "Aaron Nola", "team": "PHI", "market": "strikeouts",
         "line": 5.5, "model_over_prob": 0.52,
         "pp_line": 5.5, "pp_model_over_prob": 0.52,
         "pp_over_odds": -119, "pp_under_odds": -119},
        # consensus-only (no DFS line) -> not logged
        {"player": "Bryce Harper", "team": "PHI", "market": "total_bases",
         "line": 1.5, "model_over_prob": 0.80,
         "over_odds": -110, "under_odds": -110},
    ]}}


def test_log_qualifying_first_qualify_only(tmp_path, monkeypatch):
    monkeypatch.setattr(plays, "LOG", tmp_path / "dfs_plays.jsonl")
    new = plays.log_qualifying("2026-06-15", _blob())
    assert len(new) == 1
    leg = new[0]
    assert leg["player"] == "Zack Wheeler" and leg["side"] == "Over"
    assert leg["line"] == 5.5 and leg["line_source"] == "PrizePicks"
    assert leg["edge"] > 0.04 and leg["graded_at"] is None
    # idempotent: same slate -> nothing new logged
    assert plays.log_qualifying("2026-06-15", _blob()) == []
    assert len(plays.load_plays()) == 1


def test_closing_line_refresh_and_clv(tmp_path, monkeypatch):
    monkeypatch.setattr(plays, "LOG", tmp_path / "dfs_plays.jsonl")
    plays.log_qualifying("2026-06-15", _blob(pp_line=5.5))
    # line later moves to 5.0 (softer for the Over) -> no new row, close updated
    new = plays.log_qualifying("2026-06-15", _blob(pp_line=5.0))
    assert new == []
    p = plays.load_plays()[0]
    assert p["close_line"] == 5.0
    assert plays.line_clv(p) == 0.5  # Over: 5.5 -> 5.0 moved our way


def test_grade_plays_marks_hit_and_push(tmp_path, monkeypatch):
    monkeypatch.setattr(plays, "LOG", tmp_path / "dfs_plays.jsonl")
    plays.log_qualifying("2026-06-15", _blob())

    import project547.playerlogs as pl
    monkeypatch.setattr(pl, "actual_value", lambda *a, **k: 7.0)  # over 5.5 -> win
    n = plays.grade_plays("2026-06-15", days=4)
    assert n == 1
    p = plays.load_plays()[0]
    assert p["won"] is True and p["push"] is False and p["actual"] == 7.0
    # already graded -> not regraded
    assert plays.grade_plays("2026-06-15", days=4) == 0

    s = plays.summary()
    assert s["legs_graded"] == 1 and s["leg_win_rate"] == 1.0


def test_grade_push_when_actual_equals_line(tmp_path, monkeypatch):
    monkeypatch.setattr(plays, "LOG", tmp_path / "dfs_plays.jsonl")
    plays.log_qualifying("2026-06-15", _blob())
    import project547.playerlogs as pl
    monkeypatch.setattr(pl, "actual_value", lambda *a, **k: 5.5)  # exact line
    plays.grade_plays("2026-06-15", days=4)
    p = plays.load_plays()[0]
    assert p["push"] is True and p["won"] is None
    assert plays.summary()["leg_win_rate"] is None  # push excluded


def test_todays_card_builds_from_logged_legs(tmp_path, monkeypatch):
    monkeypatch.setattr(plays, "LOG", tmp_path / "dfs_plays.jsonl")
    # two qualifying legs on different teams -> a 2-pick card
    blob = {"MLB": {"props": [
        {"player": f"P{i}", "team": f"T{i}", "market": "ks", "line": 5.5,
         "model_over_prob": 0.4, "pp_line": 4.5, "pp_model_over_prob": 0.66,
         "pp_over_odds": -119, "pp_under_odds": -119} for i in range(2)]}}
    plays.log_qualifying("2026-06-15", blob, min_edge=0.04)
    card = plays.todays_card("2026-06-15", min_edge=0.04)
    assert card is not None and card["size"] == 2


def _two_leg_blob():
    return {"MLB": {"props": [
        {"player": f"P{i}", "team": f"T{i}", "market": "ks", "line": 5.5,
         "model_over_prob": 0.4, "pp_line": 4.5, "pp_model_over_prob": 0.66,
         "pp_over_odds": -119, "pp_under_odds": -119} for i in range(2)]}}


def test_playable_card_requires_positive_ev(tmp_path, monkeypatch):
    monkeypatch.setattr(plays, "LOG", tmp_path / "dfs_plays.jsonl")
    # single leg -> no 2+ pick slip -> no playable card
    plays.log_qualifying("2026-06-15", _blob())
    assert plays.playable_card("2026-06-15") is None
    # two strong legs -> a +EV 2-pick
    monkeypatch.setattr(plays, "LOG", tmp_path / "dfs2.jsonl")
    plays.log_qualifying("2026-06-15", _two_leg_blob())
    card = plays.playable_card("2026-06-15")
    assert card is not None and card["size"] == 2
    ev = max(x for x in (card["power_ev"], card["flex_ev"]) if x is not None)
    assert ev > 0


def test_mark_played_flags_card_legs(tmp_path, monkeypatch):
    monkeypatch.setattr(plays, "LOG", tmp_path / "dfs_plays.jsonl")
    plays.log_qualifying("2026-06-15", _two_leg_blob())
    card = plays.playable_card("2026-06-15")
    assert plays.mark_played("2026-06-15", card) == 2
    s = plays.summary()
    assert s["played_logged"] == 2 and s["legs_logged"] == 2
    # idempotent: already-played legs aren't re-flagged
    assert plays.mark_played("2026-06-15", card) == 0


def test_game_play_candidates_tiers_by_ev(tmp_path, monkeypatch):
    monkeypatch.setattr(plays.config, "SHARP_EV_MIN", 0.03)
    monkeypatch.setattr(plays.config, "SHARP_EV_MAX", 0.06)
    monkeypatch.setattr(plays.config, "STALE_EV", 0.08)
    blob = {"MLB": {"games": [{
        "home_team": "NYY", "away_team": "BOS",
        "home_ml": -150, "home_ml_ev": 0.045,     # sharp band
        "away_ml": 130, "away_ml_ev": -0.05,       # negative -> out
        "total_line": 8.5,
        "over_odds": -110, "over_ev": 0.02,        # below min -> out
        "under_odds": -110, "under_ev": 0.20,      # too high -> stale/verify
    }]}}
    cands = plays.game_play_candidates("2026-06-15", blob)
    kinds = {(c["market"], c["side"]) for c in cands}
    assert kinds == {("moneyline", "home"), ("total", "under")}
    ml = next(c for c in cands if c["market"] == "moneyline")
    assert ml["tier"] == "sharp" and ml["sharp"] is True and ml["verify"] is False
    un = next(c for c in cands if c["side"] == "under")
    assert un["tier"] == "stale" and un["verify"] is True  # 20% EV -> verify
    # a mid-band EV (between max and stale) is 'watch' — tracked, not pushed
    blob["MLB"]["games"][0]["home_ml_ev"] = 0.07
    c2 = plays.game_play_candidates("2026-06-15", blob)
    assert next(c for c in c2 if c["market"] == "moneyline")["tier"] == "watch"


def test_notified_store_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(plays, "NOTIFIED", tmp_path / "notified.json")
    assert plays.load_notified() == set()
    plays.mark_notified({"a", "b"})
    plays.mark_notified({"b", "c"})
    assert plays.load_notified() == {"a", "b", "c"}


def test_confirm_flow_played_and_skip(tmp_path, monkeypatch):
    monkeypatch.setattr(plays, "LOG", tmp_path / "dfs_plays.jsonl")
    monkeypatch.setattr(plays, "PENDING", tmp_path / "pending.json")
    monkeypatch.setattr(plays, "CONFIRM", tmp_path / "confirm.json")
    plays.log_qualifying("2026-06-15", _two_leg_blob())
    card = plays.playable_card("2026-06-15")
    keys = {plays._leg_key("2026-06-15", l) for l in card["legs"]}
    pid = plays.register_pending("dfs", "2026-06-15", keys)

    # nothing played yet
    assert plays.summary()["played_logged"] == 0
    # tap Played -> legs flagged played
    res = plays.apply_confirmations([f"played {pid}"])
    assert res["played"] == 2
    assert plays.confirmed_played() == keys
    assert plays.summary()["played_logged"] == 2
    # re-applying the same tap is idempotent
    assert plays.apply_confirmations([f"played {pid}"])["played"] == 0
    # switching to Skip clears the played flags
    plays.apply_confirmations([f"skip {pid}"])
    assert plays.summary()["played_logged"] == 0
    assert plays.confirmed_skipped() == keys


def test_apply_confirmations_ignores_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(plays, "PENDING", tmp_path / "pending.json")
    monkeypatch.setattr(plays, "CONFIRM", tmp_path / "confirm.json")
    monkeypatch.setattr(plays, "LOG", tmp_path / "dfs_plays.jsonl")
    res = plays.apply_confirmations(["played nope", "garbage", "skip"])
    assert res == {"played": 0, "skipped": 0}


def test_play_id_stable(tmp_path, monkeypatch):
    a = plays.play_id("2026-06-15", {"k1", "k2"})
    b = plays.play_id("2026-06-15", {"k2", "k1"})  # order-independent
    assert a == b and len(a) == 8


def test_played_accuracy_combines_dfs_and_games(tmp_path, monkeypatch):
    monkeypatch.setattr(plays, "LOG", tmp_path / "dfs_plays.jsonl")
    monkeypatch.setattr(plays, "CONFIRM", tmp_path / "confirm.json")
    # one played+graded DFS leg (win) and one played-but-ungraded (excluded)
    rows = [
        {"date": "2026-06-15", "sport": "MLB", "player": "A", "market": "ks",
         "side": "Over", "played": True, "graded_at": "x", "push": False, "won": True},
        {"date": "2026-06-15", "sport": "MLB", "player": "B", "market": "ks",
         "side": "Over", "played": True, "graded_at": None, "push": None, "won": None},
    ]
    monkeypatch.setattr(plays, "load_plays", lambda: rows)
    # one confirmed-played game bet (loss)
    plays.CONFIRM.write_text('{"played": ["game|2026-06-15|MLB|BOS @ NYY|moneyline|home"], "skipped": []}')
    import project547.results as results
    monkeypatch.setattr(results, "load_ledger", lambda: [
        {"date": "2026-06-15", "sport": "MLB", "game": "BOS @ NYY",
         "market": "moneyline", "side": "home", "pnl": -1.0, "won": False},
    ])
    acc, n = plays.played_accuracy()
    assert n == 2 and acc == 0.5  # 1 DFS win + 1 game loss


def test_model_accuracy_directional():
    import project547.results as results
    rows = [
        {"market": "model_winprob", "pred_home_wp": 0.7, "home_won": 1},  # right
        {"market": "model_winprob", "pred_home_wp": 0.3, "home_won": 0},  # right
        {"market": "model_winprob", "pred_home_wp": 0.6, "home_won": 0},  # wrong
        {"market": "moneyline", "pnl": 1.0, "won": True},                 # ignored
    ]
    import unittest.mock as m
    with m.patch.object(results, "load_ledger", lambda: rows):
        acc, n = results.model_accuracy()
    assert n == 3 and acc == round(2 / 3, 4)


def test_played_accuracy_date_filter(tmp_path, monkeypatch):
    monkeypatch.setattr(plays, "LOG", tmp_path / "dfs_plays.jsonl")
    monkeypatch.setattr(plays, "CONFIRM", tmp_path / "confirm.json")
    rows = [
        {"date": "2026-06-14", "sport": "MLB", "player": "A", "market": "ks",
         "side": "Over", "played": True, "graded_at": "x", "push": False, "won": True},
        {"date": "2026-06-13", "sport": "MLB", "player": "B", "market": "ks",
         "side": "Over", "played": True, "graded_at": "x", "push": False, "won": False},
    ]
    monkeypatch.setattr(plays, "load_plays", lambda: rows)
    monkeypatch.setattr(plays, "confirmed_played", lambda: set())
    assert plays.played_accuracy("2026-06-14") == (1.0, 1)   # only that day
    assert plays.played_accuracy("2026-06-13") == (0.0, 1)
    assert plays.played_accuracy() == (0.5, 2)               # cumulative


def test_record_daily_upserts_by_date(tmp_path, monkeypatch):
    monkeypatch.setattr(plays, "DAILY", tmp_path / "daily.jsonl")
    plays.record_daily("2026-06-14", (0.6, 10), (0.55, 4))
    plays.record_daily("2026-06-13", (0.5, 8), (None, 0))
    plays.record_daily("2026-06-14", (0.7, 12), (0.50, 6))  # overwrite same date
    rows = plays.load_daily_record()
    assert len(rows) == 2  # not 3 — upsert by date
    row14 = next(r for r in rows if r["date"] == "2026-06-14")
    assert row14["model_acc"] == 0.7 and row14["played_n"] == 6


def test_game_play_candidates_ranked_by_conviction(monkeypatch):
    """The board leads with the market we most reliably beat the close in
    (highest lower-bound CLV), not the biggest raw EV."""
    from project547 import config, edge_gate
    ev = (config.SHARP_EV_MIN + config.SHARP_EV_MAX) / 2   # in-band for both
    table = {
        ("MLB", "moneyline"): {"status": edge_gate.CLEARED,
                               "clv_n": config.GATE_CLEAR_MIN,
                               "avg_clv": 0.05, "clv_lb": 0.04},   # strong proven edge
        ("MLB", "total"): {"status": edge_gate.CLEARED,
                           "clv_n": config.GATE_CLEAR_MIN,
                           "avg_clv": 0.02, "clv_lb": 0.01},       # weaker proven edge
    }
    monkeypatch.setattr(edge_gate, "gate_table", lambda *a, **k: table)
    blob = {"MLB": {"games": [{
        "home_team": "NYY", "away_team": "BOS",
        "home_ml": -150, "home_ml_ev": ev,
        "total_line": 8.5, "over_odds": -110, "over_ev": ev,
    }]}}
    cands = plays.game_play_candidates("2026-06-15", blob)
    # higher-conviction market sorts first even though EVs are identical
    assert [c["market"] for c in cands] == ["moneyline", "total"]
    assert cands[0]["conviction"] == 0.04 and cands[0]["expected_clv"] == 0.05
    assert cands[1]["conviction"] == 0.01
