"""Temperature adjustment in the MLB run model (warm air -> more scoring),
and the dome/retractable-roof suppression that gates what temp the model sees."""
from project547 import config, weather
from project547.models import game


def _runs(temp):
    ti = game.TeamInputs(name="T", runs_per_game=4.6, opp_starter_xfip=4.1,
                         temp_f=temp)
    return game.expected_runs(ti, is_home=True)


def test_hot_scores_more_than_cold():
    assert _runs(95) > _runs(72) > _runs(45)


def test_none_temp_is_neutral():
    # dome / unknown temp -> no adjustment, equals the baseline temperature
    assert _runs(None) == _runs(config.TEMP_BASELINE_F)


def test_adjustment_matches_coef():
    # The multiplier applies to the pre-HFA base; HFA (+HOME_FIELD_RUNS/2) is
    # added afterward, so back it out before comparing the ratio.
    hfa = config.HOME_FIELD_RUNS / 2
    base = _runs(config.TEMP_BASELINE_F) - hfa
    hot = _runs(config.TEMP_BASELINE_F + 10) - hfa
    assert abs(hot / base - (1 + config.TEMP_COEF * 10)) < 1e-6


def test_extreme_temp_is_clamped():
    # 200F away from baseline would blow past the clamp; must be capped.
    ti = game.TeamInputs(name="T", runs_per_game=4.6, opp_starter_xfip=4.1,
                         temp_f=config.TEMP_BASELINE_F + 1000)
    base = _runs(config.TEMP_BASELINE_F)
    assert game.expected_runs(ti, True) <= base * (1 + config.TEMP_CLAMP) + 1e-9


# ---------------------------------------------------------------------------
# Dome / retractable-roof suppression (audit #7): the TEMP_COEF backtest
# excluded dome/roof games, so the live feed must never hand the model an
# outdoor forecast for a game played indoors.
# ---------------------------------------------------------------------------

def _fake_meteo(temp_f, precip_pct, monkeypatch):
    """Stub the Open-Meteo fetch with a single-hour forecast."""
    data = {"hourly": {
        "time": ["2026-07-04T23:00"],
        "temperature_2m": [temp_f],
        "precipitation_probability": [precip_pct],
        "wind_speed_10m": [8.0],
        "wind_direction_10m": [180.0],
    }}
    monkeypatch.setattr(weather, "cached_json", lambda key, ttl, fetch: data)


def test_fixed_dome_returns_none(monkeypatch):
    # Tropicana never sees weather — no forecast is even fetched.
    def boom(*a, **k):
        raise AssertionError("no forecast should be fetched for a fixed dome")
    monkeypatch.setattr(weather, "cached_json", boom)
    assert weather.game_weather("Tampa Bay Rays", "2026-07-04T23:00:00Z") is None


def test_retractable_extreme_heat_suppresses_temp(monkeypatch):
    # a 104F Phoenix forecast -> roof presumed closed -> no temp/wind
    _fake_meteo(104.0, 0, monkeypatch)
    wx = weather.game_weather("Arizona Diamondbacks", "2026-07-04T23:00:00Z")
    assert wx is not None
    assert wx["temp_f"] is None and wx["wind_mph"] is None


def test_retractable_cold_and_rain_suppress_temp(monkeypatch):
    _fake_meteo(45.0, 0, monkeypatch)   # Milwaukee in April cold
    wx = weather.game_weather("Milwaukee Brewers", "2026-04-04T23:00:00Z")
    assert wx["temp_f"] is None
    _fake_meteo(75.0, 80, monkeypatch)  # mild but 80% rain -> closed
    wx = weather.game_weather("Houston Astros", "2026-07-04T23:00:00Z")
    assert wx["temp_f"] is None


def test_retractable_mild_forecast_passes_through(monkeypatch):
    _fake_meteo(78.0, 10, monkeypatch)
    wx = weather.game_weather("Arizona Diamondbacks", "2026-05-04T23:00:00Z")
    assert wx["temp_f"] == 78 and wx["wind_mph"] == 8


def test_outdoor_park_never_suppressed(monkeypatch):
    # SEA has a canopy that never fully closes -> treated as outdoor even at
    # extreme forecasts; ordinary open parks likewise.
    _fake_meteo(96.0, 0, monkeypatch)
    for team in ("Seattle Mariners", "Boston Red Sox"):
        wx = weather.game_weather(team, "2026-07-04T23:00:00Z")
        assert wx["temp_f"] == 96
