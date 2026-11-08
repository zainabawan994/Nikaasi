"""Live weather mode — Open-Meteo is replaced with fake responses (no network)."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import engine as engine_mod
from app import weather_live
from app.engine import Engine
from app.main import create_app
from conftest import sign_in


def fake_rows(rain_by_sector: dict[str, float], forecast_by_sector: dict[str, float] | None = None):
    forecast_by_sector = forecast_by_sector or {}

    async def fake_fetch(sectors, timeout=10.0):
        out = {}
        for sid in sectors:
            r = rain_by_sector.get(sid, 0.0)
            f = forecast_by_sector.get(sid, 0.0)
            out[sid] = weather_live.parse({
                "current": {"precipitation": r / 4, "weather_code": 63 if r else 0, "time": "2026-07-20T18:45"},
                "minutely_15": {"precipitation": [r / 4, f / 4, f / 4, f / 4, f / 4]},
            })
        return out
    return fake_fetch


def test_parse_converts_15_minute_totals_to_mm_per_hour():
    d = weather_live.parse({"current": {"precipitation": 3.0, "time": "t"}, "minutely_15": {"precipitation": [3, 2, 2, 1, 1]}})
    assert d["rainMmHr"] == 12.0 and d["forecastMmHr"] == 6.0


def test_alert_levels_from_rain(monkeypatch):
    assert [weather_live.alert_for(x) for x in (0, 9.9, 10, 25, 50)] == ["none", "none", "yellow", "orange", "red"]
    monkeypatch.setenv("WEATHER_ALERT_MM", "5,15,30")
    assert weather_live.alert_for(16) == "orange"


def test_live_rain_drives_fusion(monkeypatch):
    monkeypatch.setattr(weather_live, "fetch", fake_rows({"G-10": 60.0, "G-11": 12.0}))
    e = Engine()
    e.reset(mode="live")
    asyncio.run(e.refresh_live_weather())
    w = e.weather
    assert w["G-10"]["rainMmHr"] == 60.0 and w["G-10"]["alert"] == "red" and "Open-Meteo (live)" in w["G-10"]["source"]
    assert w["G-11"]["alert"] == "yellow"
    assert w["F-8"]["rainMmHr"] == 0.0 and w["F-8"]["alert"] == "none"
    assert e.assessments["G-10"]["signals"]["weather"]["score"] > 0.8
    assert e.state()["weatherSource"]["ok"] is True
    assert e.state()["mode"] == "live"


def test_forecast_raises_the_alert_before_rain_arrives(monkeypatch):
    monkeypatch.setattr(weather_live, "fetch", fake_rows({}, {"G-10": 30.0}))
    e = Engine()
    e.reset(mode="live")
    asyncio.run(e.refresh_live_weather())
    assert e.weather["G-10"]["rainMmHr"] == 0.0 and e.weather["G-10"]["alert"] == "orange"


def test_pmd_alert_entered_by_officer_and_expiry(monkeypatch):
    monkeypatch.setattr(weather_live, "fetch", fake_rows({"G-10": 3.0}))
    e = Engine()
    e.reset(mode="live")
    asyncio.run(e.refresh_live_weather())
    e.set_weather_override("G-10", "red", 999, by="Duty Officer")  # in live mode this records a PMD alert
    assert e.weather["G-10"]["alert"] == "red" and e.weather["G-10"]["pmdAlert"] == "red"
    assert e.weather["G-10"]["rainMmHr"] == 3.0, "live rain is never overridden"
    assert "PMD alert entered by Duty Officer" in e.weather["G-10"]["source"]
    e.pmd_alerts["G-10"]["until"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    e.update_weather()
    assert e.weather["G-10"]["alert"] == "none" and "G-10" not in e.pmd_alerts


def test_fetch_failure_is_reported_and_stale_data_ignored(monkeypatch):
    monkeypatch.setattr(weather_live, "fetch", fake_rows({"G-10": 40.0}))
    e = Engine()
    e.reset(mode="live")
    asyncio.run(e.refresh_live_weather())

    async def boom(*_a, **_k):
        raise RuntimeError("network down")
    monkeypatch.setattr(weather_live, "fetch", boom)
    asyncio.run(e.refresh_live_weather())
    assert e.state()["weatherSource"]["ok"] is False
    assert any("Live weather unavailable" in ev["text"] for ev in e.events)
    assert e.weather["G-10"]["rainMmHr"] == 40.0, "recent data is still used"
    e.live["fetchedAt"] -= timedelta(minutes=engine_mod.LIVE_STALE_MIN + 1)
    e.update_weather()
    assert e.weather["G-10"]["rainMmHr"] == 0.0 and e.weather["G-10"]["source"] == "live rain unavailable"


def test_demo_story_still_works_after_live(monkeypatch):
    monkeypatch.setattr(weather_live, "fetch", fake_rows({}))
    e = Engine()
    e.reset(mode="live")
    e.reset(mode="demo")
    e.scenario = {"name": "t", "startedAt": 0, "script": [dict(s, done=False) for s in engine_mod.G10_MONSOON]}
    for _ in range(8):
        e.tick()
    assert e.mode == "demo" and e.weather["G-10"]["alert"] == "red"


def test_api_switches_modes_and_records_pmd_alert(monkeypatch):
    monkeypatch.setattr(weather_live, "fetch", fake_rows({"G-9": 20.0}))
    with TestClient(create_app(Engine())) as c:
        sign_in(c, "authority")
        assert c.post("/api/scenario", json={"action": "live"}).status_code == 200
        st = c.get("/api/state").json()
        assert st["mode"] == "live" and st["clock"] == datetime.now(engine_mod.PKT).strftime("%H:%M")
        assert c.post("/api/weather", json={"sector": "G-9", "alert": "orange"}).status_code == 200
        st = c.get("/api/state").json()
        assert st["weatherSource"]["pmdAlerts"]["G-9"]["level"] == "orange"
        assert "weather.pmd_alert" in [a["action"] for a in c.app.state.auth.list_audit()]
        assert c.post("/api/scenario", json={"action": "play"}).json()["ok"]
        assert c.get("/api/state").json()["mode"] == "demo"
        c.post("/api/scenario", json={"action": "pause"})
