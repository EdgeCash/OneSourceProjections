from onesource.clients import bettingpros as bp


def test_correlated_picks_parses_defensively():
    p = {"correlated_picks": [
        {"participant": {"name": "Aaron Judge"}, "market": {"name": "Total Bases"},
         "recommended_side": "over", "selection": {"line": 1.5, "cost": -120},
         "correlation": 0.41},
        {"player": {"name": "Juan Soto"}, "market_name": "Hits", "side": "under",
         "line": 0.5, "cost": 100, "correlation_coefficient": -0.18},
    ]}
    out = bp._correlated_picks(p)
    assert len(out) == 2
    a, b = out
    assert a["player"] == "Aaron Judge" and a["side"] == "over"
    assert a["line"] == 1.5 and a["odds"] == -120 and a["correlation"] == 0.41
    assert b["player"] == "Juan Soto" and b["correlation"] == -0.18


def test_correlated_picks_missing_or_bad_shape():
    assert bp._correlated_picks({}) == []
    assert bp._correlated_picks({"correlated_picks": None}) == []
    assert bp._correlated_picks({"correlated_picks": "nope"}) == []
    # entries that aren't dicts are skipped; unknown fields -> None, not error
    out = bp._correlated_picks({"correlated_picks": ["x", {"foo": "bar"}]})
    assert len(out) == 1
    assert out[0]["player"] is None and out[0]["correlation"] is None


def test_flatten_props_includes_correlated_picks():
    raw = [{"participant": {"name": "X", "player": {"team": "NYY"}},
            "line": 1.5,
            "correlated_picks": [{"participant": "Teammate", "side": "over"}]}]
    row = bp.flatten_props(raw)[0]
    assert "correlated_picks" in row
    assert row["correlated_picks"][0]["player"] == "Teammate"
