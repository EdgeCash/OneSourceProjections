import math

from onesource import experts


def _prop(**kw):
    base = {"player": "A. Judge", "team": "NYY", "market": "batter_hits",
            "line": 1.5, "model_over_prob": float("nan"),
            "bp_recommended_side": None, "bp_bet_rating": float("nan"),
            "bp_ev": float("nan"), "ev": None, "pick_pct_over": float("nan")}
    base.update(kw)
    return base


def test_model_side_thresholds():
    assert experts._model_side({"model_over_prob": 0.60}) == "Over"
    assert experts._model_side({"model_over_prob": 0.40}) == "Under"
    assert experts._model_side({"model_over_prob": 0.50}) is None  # too close
    assert experts._model_side({"model_over_prob": float("nan")}) is None


def test_norm_side():
    assert experts._norm_side("over") == "Over"
    assert experts._norm_side("UNDER") == "Under"
    assert experts._norm_side("") is None
    assert experts._norm_side(None) is None


def test_public_side_needs_clear_lean():
    assert experts._public_side({"pick_pct_over": 0.62}) == "Over"
    assert experts._public_side({"pick_pct_over": 0.35}) == "Under"
    assert experts._public_side({"pick_pct_over": 0.52}) is None


def test_prop_consensus_unanimous():
    p = _prop(model_over_prob=0.62, bp_recommended_side="over",
              pick_pct_over=0.71, bp_bet_rating=4.0, ev=0.06)
    row = experts.prop_consensus(p)
    assert row["consensus"] == "Over"
    assert row["n_sources"] == 3 and row["agree"] == 3
    assert row["unanimous"] is True
    assert row["bp_rating"] == 4.0


def test_prop_consensus_split():
    p = _prop(model_over_prob=0.62, bp_recommended_side="under",
              pick_pct_over=0.5)  # public neutral
    row = experts.prop_consensus(p)
    # model Over, BP Under, public neutral -> 2 sources, 1-1, no majority
    assert row["n_sources"] == 2
    assert row["agree"] == 1
    assert row["consensus"] is None
    assert row["unanimous"] is False


def test_prop_consensus_none_when_no_signal():
    assert experts.prop_consensus(_prop()) is None


def test_consensus_table_filters_and_sorts():
    slate = {"MLB": {"props": [
        _prop(player="Judge", model_over_prob=0.62, bp_recommended_side="over",
              pick_pct_over=0.7, bp_bet_rating=5.0, ev=0.05),          # 3 agree
        _prop(player="Soto", model_over_prob=0.58, bp_recommended_side="over",
              bp_bet_rating=2.0, ev=0.02),                            # 2 agree
        _prop(player="Cole", model_over_prob=0.50),                   # no lean
    ]}}
    rows = experts.consensus_table(slate)
    assert [r["player"] for r in rows] == ["Judge", "Soto"]  # Cole dropped, sorted
    # multi_only keeps both (each has >=2 sources)
    assert len(experts.consensus_table(slate, multi_only=True)) == 2
    # search narrows to one
    assert [r["player"] for r in experts.consensus_table(slate, query="soto")] == ["Soto"]
    every = experts.consensus_table(slate)
    assert every[0]["player"] == "Judge"  # 3 sources sorts above 2


def test_consensus_table_handles_empty():
    assert experts.consensus_table({}) == []
    assert experts.consensus_table({"MLB": {}}) == []
