"""internal_stats data-integrity (audit #9): doubleheader retention, same-name
player separation, id-keyed aggregation, and cache housekeeping."""
import pandas as pd
import pytest

from project547 import history, internal_stats as istat, playerlogs


def _bf_row(name, pid, game_pk, date, team="NYY", position="P", started=True,
            **stats):
    base = {"strikeOuts": 5, "battersFaced": 22, "inningsPitched": 6.0,
            "baseOnBalls": 1, "hitByPitch": 0, "homeRuns": 1, "hits": 5,
            "earnedRuns": 2, "totalBases": 0, "atBats": 0,
            "plateAppearances": 0}
    base.update(stats)
    return {"player_name": name, "player_id": pid, "team": team,
            "position": position, "started": started, "date": date,
            "game_pk": game_pk, "stats": base}


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    """Route both sources somewhere controllable and keep lru_caches clean."""
    monkeypatch.setattr(playerlogs, "FORWARD_DIR", tmp_path)
    istat.clear_caches()
    yield tmp_path
    istat.clear_caches()


def test_doubleheader_both_games_survive(monkeypatch, isolated):
    rows = [
        _bf_row("Ace Starter", 100, 111, "2026-06-01", strikeOuts=7),
        _bf_row("Ace Starter", 100, 222, "2026-06-01", strikeOuts=3),  # game 2
    ]
    monkeypatch.setattr(history, "player_games",
                        lambda sk, seasons=None: pd.DataFrame(rows))
    df = istat._mlb_rows(2026)
    assert len(df) == 2                       # old (name, date) key kept 1
    pt = istat.pitcher_table(2026)
    assert len(pt) == 1
    assert int(pt.iloc[0]["GS"]) == 2         # both starts aggregate
    assert float(pt.iloc[0]["IP"]) == 12.0


def test_same_named_players_stay_separate(monkeypatch, isolated):
    rows = [
        _bf_row("Will Smith", 100, 111, "2026-06-01", team="LAD",
                position="C", started=True, atBats=4, hits=2,
                plateAppearances=4, totalBases=3),
        _bf_row("Will Smith", 200, 111, "2026-06-01", team="ATL",
                position="C", started=True, atBats=4, hits=0,
                plateAppearances=4, totalBases=0),
    ]
    monkeypatch.setattr(history, "player_games",
                        lambda sk, seasons=None: pd.DataFrame(rows))
    df = istat._mlb_rows(2026)
    assert len(df) == 2
    bt = istat.batter_table(2026)
    assert len(bt) == 2                        # one row per player_id
    assert set(bt["norm_name"]) == {"will smith"}  # names survive for lookups
    assert sorted(bt["AVG"]) == [0.0, 0.5]


def test_exact_duplicates_still_deduped(monkeypatch, isolated):
    rows = [
        _bf_row("Ace Starter", 100, 111, "2026-06-01"),
        _bf_row("Ace Starter", 100, 111, "2026-06-01"),   # same player+game
    ]
    monkeypatch.setattr(history, "player_games",
                        lambda sk, seasons=None: pd.DataFrame(rows))
    assert len(istat._mlb_rows(2026)) == 1


def test_idless_forward_row_shadowed_by_id_row(monkeypatch, isolated):
    # an old forward-store row (no player_id) duplicating an id-carrying
    # backfill row for the same (name, game) must not double-count
    monkeypatch.setattr(history, "player_games", lambda sk, seasons=None:
                        pd.DataFrame([_bf_row("Ace Starter", 100, 111,
                                              "2026-06-01")]))
    fwd = isolated / "mlb.jsonl"
    fwd.write_text(pd.DataFrame([{
        "name": "Ace Starter", "date": "2026-06-01", "season": 2026,
        "game_pk": 111, "strikeOuts": 5, "hits": 5,
    }]).to_json(orient="records", lines=True))
    df = istat._mlb_rows(2026)
    assert len(df) == 1
    assert df["player_id"].notna().all()      # the id-carrying row won


def test_idless_fallback_key_is_name_date(monkeypatch, isolated):
    # rows with neither player_id nor game_pk still dedupe on (name, date)
    monkeypatch.setattr(history, "player_games",
                        lambda sk, seasons=None: pd.DataFrame())
    fwd = isolated / "mlb.jsonl"
    fwd.write_text(pd.DataFrame([
        {"name": "Old Row", "date": "2026-06-01", "season": 2026, "hits": 1},
        {"name": "Old Row", "date": "2026-06-01", "season": 2026, "hits": 2},
    ]).to_json(orient="records", lines=True))
    df = istat._mlb_rows(2026)
    assert len(df) == 1 and df.iloc[0]["hits"] == 2   # keep="last"


def test_clear_caches_runs_and_pitcher_table_cached(monkeypatch, isolated):
    calls = {"n": 0}

    def fake_rows(season):
        calls["n"] += 1
        return pd.DataFrame()

    monkeypatch.setattr(istat, "_mlb_rows", fake_rows)
    istat.pitcher_table(2031)
    istat.pitcher_table(2031)
    assert calls["n"] == 1                    # lru_cache present (audit fix)
    istat.clear_caches()                      # must not raise
    istat.pitcher_table(2031)
    assert calls["n"] == 2
