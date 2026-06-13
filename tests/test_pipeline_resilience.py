"""The per-sport pipeline must keep games even when the props step fails (and
vice-versa) so a transient data KeyError can't blank a whole sport's slate."""

from onesource import pipeline


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
