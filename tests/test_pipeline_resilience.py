"""The per-sport pipeline must keep games even when the props step fails (and
vice-versa) so a transient data KeyError can't blank a whole sport's slate."""

from project547 import pipeline


class _FakeDF:
    def __init__(self, rows):
        self._rows = rows

    def to_dict(self, orient="records"):
        assert orient == "records"
        return self._rows


def test_records_handles_df_and_list():
    assert pipeline._records(_FakeDF([{"a": 1}])) == [{"a": 1}]
    assert pipeline._records([{"b": 2}]) == [{"b": 2}]
    assert pipeline._records(None) == []


def test_safe_step_success():
    recs, err = pipeline._safe_step(lambda: _FakeDF([{"x": 1}]), "games", "MLB")
    assert recs == [{"x": 1}] and err is None


def test_safe_step_failure_is_captured_not_raised():
    def boom():
        raise KeyError(0)  # the real-world symptom: str(KeyError(0)) == "0"
    recs, err = pipeline._safe_step(boom, "props", "MLB")
    assert recs == []
    assert err is not None and "props unavailable" in err and "KeyError" in err


def test_bundle_keeps_games_when_props_fail():
    games, _ = pipeline._safe_step(lambda: _FakeDF([{"g": 1}]), "games", "MLB")
    props, pe = pipeline._safe_step(lambda: (_ for _ in ()).throw(KeyError(0)),
                                    "props", "MLB")
    out = pipeline._bundle(games, props, None, pe)
    assert out["games"] == [{"g": 1}]      # games survive the props failure
    assert out["props"] == []
    assert "props unavailable" in out["error"]


def test_bundle_no_error_key_when_clean():
    out = pipeline._bundle([{"g": 1}], [{"p": 1}], None, None)
    assert "error" not in out


# --------------------------------------------------------------------------
# Budget gate: no games on the slate => zero BettingPros prop calls
# --------------------------------------------------------------------------
def test_generic_skips_props_when_no_games(monkeypatch):
    import pandas as pd
    called = {"props": 0}
    monkeypatch.setattr(pipeline, "project_generic_games",
                        lambda sk, d: pd.DataFrame())          # no games
    monkeypatch.setattr(pipeline, "attach_generic_game_edges",
                        lambda g, sk, d: g)
    def _spy(sk, d):
        called["props"] += 1
        return pd.DataFrame()
    monkeypatch.setattr(pipeline, "project_generic_props", _spy)

    out = pipeline._run_generic("NHL", "2026-06-27")
    assert out["games"] == [] and out["props"] == []
    assert called["props"] == 0          # BettingPros prop path never touched


def test_mlb_skips_props_when_no_games(monkeypatch):
    import pandas as pd
    called = {"props": 0}
    monkeypatch.setattr(pipeline, "project_games", lambda d: pd.DataFrame())
    monkeypatch.setattr(pipeline, "attach_game_edges", lambda g, d: g)
    def _spy(d):
        called["props"] += 1
        return pd.DataFrame()
    monkeypatch.setattr(pipeline, "project_props", _spy)

    out = pipeline._run_mlb("2026-06-27")
    assert out["games"] == [] and out["props"] == []
    assert called["props"] == 0


def _stub_snapshot_writes(monkeypatch, snapshots):
    # keep tests from touching real data/history files
    for fn in ("_persist_json", "_save_raw_sample", "_append"):
        monkeypatch.setattr(snapshots, fn, lambda *a, **k: None)


def test_snapshot_skips_bettingpros_when_no_events(monkeypatch):
    from project547 import snapshots
    from project547.clients import bettingpros
    _stub_snapshot_writes(monkeypatch, snapshots)
    calls = {"props": 0, "offers": 0, "markets": 0}
    monkeypatch.setattr(bettingpros, "events", lambda sk, d: [])   # no games
    monkeypatch.setattr(bettingpros, "props",
                        lambda *a, **k: calls.__setitem__("props", calls["props"] + 1) or [])
    monkeypatch.setattr(bettingpros, "markets",
                        lambda sk: calls.__setitem__("markets", calls["markets"] + 1) or [])
    monkeypatch.setattr(bettingpros, "offers",
                        lambda *a, **k: calls.__setitem__("offers", calls["offers"] + 1) or {})
    counts = snapshots.snapshot("2026-06-27", sports=["NBA"])
    assert counts.get("NBA") == 0
    assert calls == {"props": 0, "offers": 0, "markets": 0}   # zero BP spend


# --------------------------------------------------------------------------
# Prop-pull window (budget guard) — props gated to ET hours
# --------------------------------------------------------------------------
def test_props_window_open():
    from project547 import config
    lo, hi = config.PROPS_WINDOW_ET
    assert config.props_window_open(lo) and config.props_window_open(hi)
    assert config.props_window_open((lo + hi) // 2)
    assert not config.props_window_open(lo - 1)
    assert not config.props_window_open(hi + 1)
    assert not config.props_window_open(3)      # 3am: closed


def test_generic_skips_props_when_window_closed(monkeypatch):
    import pandas as pd
    called = {"props": 0}
    monkeypatch.setattr(pipeline, "project_generic_games",
                        lambda sk, d: pd.DataFrame([{"home_team": "A", "away_team": "B"}]))
    monkeypatch.setattr(pipeline, "attach_generic_game_edges", lambda g, sk, d: g)
    monkeypatch.setattr(pipeline, "project_generic_props",
                        lambda sk, d: called.__setitem__("props", called["props"] + 1) or pd.DataFrame())
    # games present but window closed -> games kept, props skipped
    out = pipeline._run_generic("WNBA", "2026-06-27", pull_props=False)
    assert out["games"] and out["props"] == []
    assert called["props"] == 0


def test_snapshot_window_skips_props_keeps_games(monkeypatch):
    from project547 import snapshots
    from project547.clients import bettingpros, oddsapi
    _stub_snapshot_writes(monkeypatch, snapshots)
    calls = {"props": 0}
    monkeypatch.setattr(bettingpros, "events", lambda sk, d: [{"id": "1"}])
    monkeypatch.setattr(bettingpros, "markets", lambda sk: [])
    monkeypatch.setattr(bettingpros, "game_market_ids", lambda sk: {})
    monkeypatch.setattr(bettingpros, "props",
                        lambda *a, **k: calls.__setitem__("props", calls["props"] + 1) or [])
    monkeypatch.setattr(oddsapi, "game_odds", lambda sk: [])
    monkeypatch.setattr(oddsapi, "snapshot_rows", lambda *a, **k: [])
    snapshots.snapshot("2026-06-27", sports=["MLB"], pull_props=False)
    assert calls["props"] == 0                         # props skipped
    snapshots.snapshot("2026-06-27", sports=["MLB"], pull_props=True)
    assert calls["props"] == 1                         # pulled when open
