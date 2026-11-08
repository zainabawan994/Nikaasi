"""
PostgreSQL persistence tests. Run against a throwaway database:

    set NIKAASI_TEST_DATABASE_URL=postgresql://postgres:YOUR_POSTGRES_PASSWORD@localhost:5432/nikaasi_test
    python -m pytest tests/test_db.py

Skipped when the variable is not set. The tables in that database are dropped and recreated.
"""
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import db
from app.engine import Engine
from app.main import create_app
from app.scenario import G10_MONSOON
from conftest import sign_in

URL = os.environ.get("NIKAASI_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="NIKAASI_TEST_DATABASE_URL not set")


@pytest.fixture()
def store():
    from sqlalchemy import text
    s = db.Store(URL, migrate=False)
    assert s.enabled, s.error
    db.metadata.drop_all(s.engine)
    with s.engine.begin() as c:
        c.execute(text("DROP TABLE IF EXISTS alembic_version"))
    from app.migrate import upgrade
    upgrade(s.engine)  # build the schema exactly as production does
    yield s
    s.close()


def count(store, table, *where):
    with store.engine.connect() as c:
        return c.execute(select(func.count()).select_from(table).where(*where)).scalar_one()


def test_full_story_is_persisted(store):
    e = Engine(store=store)
    run_id = store.run_id
    e.scenario = {"name": "test", "startedAt": 0, "script": [dict(s, done=False) for s in G10_MONSOON]}
    for _ in range(14):
        e.tick()
    inc = e.active_incident("G-10")
    e.execute(inc["id"], "test")
    for _ in range(90):
        if inc["status"] == "resolved":
            break
        e.tick()
    store.flush()

    assert count(store, db.runs) == 1
    assert count(store, db.reports, db.reports.c.run_id == run_id) == len(e.reports) == 6
    assert count(store, db.tickets, db.tickets.c.status == "Resolved") == 2
    assert count(store, db.broadcasts, db.broadcasts.c.kind == "warning") >= 1
    assert count(store, db.broadcasts, db.broadcasts.c.kind == "all_clear") >= 1
    assert count(store, db.trail, db.trail.c.incident_code == inc["id"]) == len(inc["trail"])
    assert count(store, db.events) == len(e.events) - 1  # the startup "online" line is not stored

    with store.engine.connect() as c:
        row = c.execute(select(db.incidents).where(db.incidents.c.code == inc["id"])).mappings().one()
    assert row["sector"] == "G-10"
    assert row["status"] == "resolved"
    assert row["peak_severity"] == "Critical"
    assert row["plan"]["actions"][0]["type"] == "reroute"
    assert row["impact"]["before"]["metrics"]["exposurePerHr"] > row["impact"]["after"]["metrics"]["exposurePerHr"]


def test_reset_starts_a_new_run_and_history_api(store):
    with TestClient(create_app(Engine(store=store))) as c:
        first = store.run_id
        r = c.post("/api/reports", json={"sector": "G-10", "text": "Pani bhar gaya hai", "reporterId": "u1",
                                         "photo": "data:image/png;base64,iVBORw0KGgo="})
        assert r.status_code == 200
        sign_in(c, "admin")
        c.post("/api/scenario", json={"action": "reset"})
        assert store.run_id != first
        store.flush()

        runs = c.get("/api/history/runs").json()
        assert [x["id"] for x in runs] == [str(first)], "an empty run (no records yet) is not stored"

        reps = c.get("/api/history/reports", params={"run": str(first)}).json()
        assert len(reps) == 1 and reps[0]["has_photo"] is True and reps[0]["sector"] == "G-10"
        assert c.get("/api/history/reports", params={"run": "nope"}).status_code == 400

        st = c.get("/api/db/status").json()
        assert st["enabled"] is True and st["counts"]["reports"] == 1 and st["counts"]["users"] == 1
        c.post("/api/auth/logout")
        assert c.get("/api/history/runs").status_code == 401, "history needs a signed-in user"
        assert c.get("/api/state").json()["db"]["enabled"] is True


def test_unreachable_database_falls_back_to_memory():
    s = db.Store("postgresql://nobody@127.0.0.1:1/none")
    assert s.enabled is False and s.error
    e = Engine(store=s)
    e.tick()  # no exceptions, in-memory as before
    with TestClient(create_app(e)) as c:
        sign_in(c, "authority")  # accounts fall back to memory too
        assert c.get("/api/history/runs").status_code == 503
