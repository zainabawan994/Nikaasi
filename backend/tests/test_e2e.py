"""End-to-end: signal intake -> fusion -> plan -> execution -> outcome, plus the HTTP API."""
import os

os.environ["GEMINI_API_KEY"] = ""  # deterministic, offline (empty also stops .env from loading a real key)

from fastapi.testclient import TestClient  # noqa: E402

from app.engine import Engine  # noqa: E402
from app.main import create_app  # noqa: E402
from app.scenario import G10_MONSOON  # noqa: E402
from conftest import sign_in  # noqa: E402



def manual_scenario() -> Engine:
    e = Engine()
    e.scenario = {"name": "test", "startedAt": 0, "script": [dict(s, done=False) for s in G10_MONSOON]}
    return e


def test_g10_story_detect_plan_execute_improve_resolve():
    e = manual_scenario()
    for _ in range(14):
        e.tick()
    inc = e.active_incident("G-10")
    assert inc, "incident opened for G-10"
    assert inc["severity"] == "Critical"
    assert e.assessments["G-10"]["final"]["confidence"] == "High"
    kinds = {a["id"] for a in inc["plan"]["actions"]}
    assert {"reroute", "dispatch_drain", "dispatch_rescue", "alert"} <= kinds
    assert e.active_incident("F-8") is None, "one uncorroborated report must not open an incident"

    before = dict(e.traffic_now["metrics"])
    e.execute(inc["id"], "test")
    after = e.traffic_now["metrics"]
    assert after["closedLinks"] >= 1
    assert after["exposurePerHr"] < before["exposurePerHr"] * 0.2
    assert after["avgTripMin"] < before["avgTripMin"]
    assert len([t for t in e.tickets if t["incidentId"] == inc["id"]]) == 2
    assert any(b["incidentId"] == inc["id"] and b["delivered"] > 1000 for b in e.broadcasts)

    for _ in range(90):
        if inc["status"] == "resolved":
            break
        e.tick()
    assert inc["status"] == "resolved"
    assert e.depth["G-10"] < e.shadow_depth["G-10"], "response beats the no-action counterfactual"
    assert all(t["status"] == "Resolved" for t in e.tickets)
    assert not e.closed, "roads reopened"
    assert len(inc["trail"]) >= 5


def test_http_api_report_auth_execute():
    with TestClient(create_app(Engine())) as c:
        r = c.post("/api/reports", json={"sector": "G-10", "text": "Pani bhar gaya hai, gaari phans gayi hai", "reporterId": "u1"})
        assert r.status_code == 200
        j = r.json()
        assert j["report"]["ai"]["hazard"] == "vehicle_stranded"
        assert j["sectorStatus"]["severity"] == "Watch"

        assert c.post("/api/reports", json={"sector": "Z-99", "text": "pani"}).status_code == 400
        r = c.post("/api/reports", json={"lat": 33.676, "lng": 73.015, "text": "Road band hai", "reporterId": "u2"})
        assert r.json()["report"]["sector"] == "G-10", "GPS snaps to sector"

        assert c.post("/api/scenario", json={"action": "step"}).status_code == 401
        sign_in(c, "authority")

        assert c.post("/api/weather", json={"sector": "G-10", "alert": "red", "rainMmHr": 60}).status_code == 200
        c.post("/api/reports", json={"sector": "G-10", "text": "Underpass mein pani, road band hai", "reporterId": "u3"})
        st = c.get("/api/state").json()
        inc = next(i for i in st["incidents"] if i["sector"] == "G-10")
        assert c.post(f"/api/incidents/{inc['id']}/execute").status_code == 200
        assert c.post(f"/api/incidents/{inc['id']}/execute").status_code == 409

        assert len(c.get("/api/alerts", params={"sector": "G-10"}).json()) >= 1
        assert c.get("/api/feeds/weather").json()["sectors"]["G-10"]["alert"] == "red"
        assert "links" in c.get("/api/feeds/traffic").json()
        c.post("/api/auth/logout")
        assert "incidents" not in c.get("/api/state").json(), "public state hides authority data"
        sign_in(c, "authority")
        # play / pause must start and stop the async clock without errors
        r = c.post("/api/scenario", json={"action": "play"})
        assert r.status_code == 200 and r.json()["running"] is True
        r = c.post("/api/scenario", json={"action": "pause"})
        assert r.status_code == 200 and r.json()["running"] is False
        assert c.post("/api/scenario", json={"action": "fly"}).status_code == 400

        assert c.get("/api/nope").status_code == 404
        assert c.get("/").status_code == 200 and "Nikaasi" in c.get("/").text
