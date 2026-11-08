"""
Live rainfall for every sector from Open-Meteo (https://open-meteo.com).

  * free, no API key; data © Open-Meteo, CC BY 4.0 (attribution shown in the UI)
  * free tier is for non-commercial use — production/commercial use needs their paid plan
  * one request covers all 12 sector centres

PMD (Pakistan Meteorological Department) has no public API, so official PMD
alerts are entered by a duty officer (see Engine.set_pmd_alert). Rain-based
alert levels below follow common intensity bands and can be tuned in .env.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import httpx

URL = "https://api.open-meteo.com/v1/forecast"
PROVIDER = "Open-Meteo"
ATTRIBUTION = "Weather data by Open-Meteo.com (CC BY 4.0)"
LEVELS = ("none", "yellow", "orange", "red")


def thresholds() -> tuple[float, float, float]:
    """mm/h at which rain alone raises a yellow / orange / red level (WEATHER_ALERT_MM="10,25,50")."""
    try:
        y, o, r = (float(x) for x in os.environ.get("WEATHER_ALERT_MM", "10,25,50").split(","))
        return y, o, r
    except ValueError:
        return 10.0, 25.0, 50.0


def alert_for(rain_mm_hr: float) -> str:
    y, o, r = thresholds()
    return "red" if rain_mm_hr >= r else "orange" if rain_mm_hr >= o else "yellow" if rain_mm_hr >= y else "none"


def max_level(*levels: str) -> str:
    return max(levels, key=LEVELS.index)


async def fetch(sectors: dict[str, dict], timeout: float = 10.0) -> dict[str, dict]:
    """Return {sector: {rainMmHr, forecastMmHr, weatherCode, observedAt}} for all sectors."""
    ids = list(sectors)
    params = {
        "latitude": ",".join(f"{sectors[s]['latlng'][0]:.4f}" for s in ids),
        "longitude": ",".join(f"{sectors[s]['latlng'][1]:.4f}" for s in ids),
        "current": "precipitation,rain,weather_code",
        "minutely_15": "precipitation",
        "forecast_minutely_15": 5,     # current quarter-hour + the next hour
        "timezone": "Asia/Karachi",
    }
    async with httpx.AsyncClient(timeout=timeout, headers={"user-agent": "Nikaasi flood-response (open-source)"}) as c:
        r = await c.get(URL, params=params)
    if r.status_code != 200:
        raise RuntimeError(f"Open-Meteo HTTP {r.status_code}")
    data = r.json()
    rows = data if isinstance(data, list) else [data]
    if len(rows) != len(ids):
        raise RuntimeError(f"Open-Meteo returned {len(rows)} locations, expected {len(ids)}")
    return {sid: parse(row) for sid, row in zip(ids, rows)}


def parse(row: dict) -> dict:
    cur = row.get("current") or {}
    # `current.precipitation` is the total for the current 15-minute interval → ×4 = mm/h
    rate = float(cur.get("precipitation") or 0) * 4
    q = (row.get("minutely_15") or {}).get("precipitation") or []
    next_hour = sum(float(x or 0) for x in q[1:5])  # mm expected in the next hour
    return {
        "rainMmHr": round(rate, 1),
        "forecastMmHr": round(next_hour, 1),
        "weatherCode": cur.get("weather_code"),
        "observedAt": cur.get("time"),
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
    }
