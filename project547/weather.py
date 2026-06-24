"""Game-time ballpark weather via Open-Meteo (free, no key). Returns the
forecast hour nearest first pitch: temp (F), wind (mph + direction),
precipitation probability."""

from __future__ import annotations

from datetime import datetime, timezone

import requests

from .cache import cached_json
from .teams import canon

# canonical MLB team -> (lat, lon) of home park (approx center field)
PARKS = {
    "ARI": (33.4455, -112.0667), "ATL": (33.8908, -84.4678),
    "BAL": (39.2840, -76.6216), "BOS": (42.3467, -71.0972),
    "CHC": (41.9484, -87.6553), "CWS": (41.8299, -87.6338),
    "CIN": (39.0975, -84.5066), "CLE": (41.4962, -81.6852),
    "COL": (39.7559, -104.9942), "DET": (42.3390, -83.0485),
    "HOU": (29.7572, -95.3556), "KC": (39.0517, -94.4803),
    "LAA": (33.8003, -117.8827), "LAD": (34.0739, -118.2400),
    "MIA": (25.7781, -80.2196), "MIL": (43.0280, -87.9712),
    "MIN": (44.9817, -93.2776), "NYM": (40.7571, -73.8458),
    "NYY": (40.8296, -73.9262), "ATH": (38.5816, -121.4944),
    "PHI": (39.9061, -75.1665), "PIT": (40.4469, -80.0057),
    "SD": (32.7076, -117.1570), "SEA": (47.5914, -122.3325),
    "SF": (37.7786, -122.3893), "STL": (38.6226, -90.1928),
    "TB": (27.7682, -82.6534), "TEX": (32.7473, -97.0832),
    "TOR": (43.6414, -79.3894), "WSH": (38.8730, -77.0074),
}

_DIRS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
         "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def game_weather(home_team: str, game_time_iso: str | None) -> dict | None:
    """{temp_f, wind_mph, wind_dir, precip_pct} at the hour nearest first
    pitch, or None when the park or forecast is unavailable."""
    key = canon("MLB", home_team)
    if key not in PARKS or not game_time_iso:
        return None
    lat, lon = PARKS[key]
    try:
        gt = datetime.fromisoformat(str(game_time_iso).replace("Z", "+00:00"))
    except ValueError:
        return None

    def fetch():
        r = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={"latitude": lat, "longitude": lon,
                    "hourly": "temperature_2m,precipitation_probability,"
                              "wind_speed_10m,wind_direction_10m",
                    "temperature_unit": "fahrenheit", "wind_speed_unit": "mph",
                    "forecast_days": 3, "timezone": "UTC"},
            timeout=20)
        r.raise_for_status()
        return r.json()

    try:
        data = cached_json(f"meteo:{key}:{gt.date()}", 3 * 3600, fetch)
        hours = data.get("hourly", {})
        times = hours.get("time", [])
        if not times:
            return None
        target = gt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:00")
        idx = times.index(target) if target in times else min(
            range(len(times)), key=lambda i: abs(
                datetime.fromisoformat(times[i]).replace(tzinfo=timezone.utc)
                - gt))
        wd = hours["wind_direction_10m"][idx]
        return {
            "temp_f": round(hours["temperature_2m"][idx]),
            "wind_mph": round(hours["wind_speed_10m"][idx]),
            "wind_dir": _DIRS[int(((wd or 0) + 11.25) // 22.5) % 16],
            "precip_pct": hours["precipitation_probability"][idx],
        }
    except Exception:
        return None
