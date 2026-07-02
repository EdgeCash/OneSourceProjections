"""Home-plate umpire assignment + tendency (committed table + model hooks)."""
from __future__ import annotations

import json

from project547 import config, umpires
from project547.models import game as game_model
from project547.models import props


def _fake_table(tmp_path, monkeypatch):
    tbl = tmp_path / "umps.json"
    tbl.write_text(json.dumps({
        "season": 2026, "league": {"runs_pg": 9.0, "k_pg": 16.75},
        "umpires": {
            "Hi Runs": {"id": 1, "games": 40, "runs_idx": 1.10, "k_idx": 0.95},
            "Lo Runs": {"id": 2, "games": 40, "runs_idx": 0.90, "k_idx": 1.05},
        },
    }))
    monkeypatch.setattr(umpires, "_TABLE_PATH", tbl)
    umpires._table.cache_clear()


def test_tendency_lookup(tmp_path, monkeypatch):
    _fake_table(tmp_path, monkeypatch)
    assert umpires.tendency("Hi Runs")["runs_idx"] == 1.10
    assert umpires.tendency("hi runs")["runs_idx"] == 1.10   # name-tolerant
    assert umpires.tendency("Never Seen") is None
    assert umpires.tendency(None) is None


def test_factors_off_by_default_are_neutral(tmp_path, monkeypatch):
    _fake_table(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "UMPIRE_RUNS_WEIGHT", 0.0)
    monkeypatch.setattr(config, "UMPIRE_K_WEIGHT", 0.0)
    # context-only default: tendency is known but the model factor is neutral
    assert umpires.runs_factor("Hi Runs") == 1.0
    assert umpires.k_factor("Lo Runs") == 1.0


def test_factors_weight_and_clamp(tmp_path, monkeypatch):
    _fake_table(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "UMPIRE_RUNS_WEIGHT", 0.5)
    monkeypatch.setattr(config, "UMPIRE_RUNS_CLAMP", 0.035)
    # idx 1.10 -> +10%, half-weighted +5%, clamped to +3.5%
    assert umpires.runs_factor("Hi Runs") == 1.035
    # idx 0.90 -> -10%, half-weighted -5%, clamped to -3.5%
    assert umpires.runs_factor("Lo Runs") == 0.965
    # unknown ump -> neutral even with weight on
    assert umpires.runs_factor("Never Seen") == 1.0


def test_runs_factor_moves_game_total():
    # the model hook: a >1 ump factor lifts the total, <1 lowers it, and it
    # moves the total symmetrically (not the margin).
    base = game_model.TeamInputs(name="X", runs_per_game=4.5, opp_starter_xfip=None)
    hot = game_model.TeamInputs(name="X", runs_per_game=4.5, opp_starter_xfip=None,
                                ump_runs_factor=1.035)
    assert game_model.expected_runs(hot, is_home=True) > \
        game_model.expected_runs(base, is_home=True)


def test_k_factor_moves_strikeout_prop():
    neutral = props.pitcher_strikeouts(6.0, 0.25)["mean"]
    boosted = props.pitcher_strikeouts(6.0, 0.25, ump_k_factor=1.06)["mean"]
    assert boosted > neutral
    assert abs(boosted / neutral - 1.06) < 1e-9
