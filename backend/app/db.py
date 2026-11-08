"""
PostgreSQL persistence for Nikaasi (SQLAlchemy Core + psycopg2).

Set DATABASE_URL, e.g.  postgresql://postgres@localhost:5432/nikaasi
Without it (or if the server is unreachable) the app runs purely in memory.

Design
  * Every Play / Reset starts a new *run* (uuid); all rows carry run_id so
    each demo's history stays separate.
  * The engine only enqueues writes (non-blocking). A single background
    thread owns the connection and applies them in order, so the simulation
    clock never waits on the database.
  * The schema is managed by Alembic migrations (backend/migrations), applied on startup.
"""
from __future__ import annotations

import base64
import json
import logging
import queue
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import (Boolean, CheckConstraint, Column, DateTime, Float, ForeignKey, Integer, LargeBinary, MetaData, String, Table, Text,
                        UniqueConstraint, create_engine, func, select)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID, insert

log = logging.getLogger("nikaasi.db")
metadata = MetaData()


def _snap(x):
    """Detached JSON copy: the engine keeps mutating its dicts while the writer thread serialises."""
    return None if x is None else json.loads(json.dumps(x))


def _now() -> datetime:
    return datetime.now(timezone.utc)


runs = Table(
    "runs", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("scenario", String(80)),
    Column("started_at", DateTime(timezone=True), nullable=False, default=_now),
)

reports = Table(
    "reports", metadata,
    Column("id", Integer, primary_key=True),
    Column("run_id", UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("code", String(16), nullable=False),
    Column("sector", String(8), nullable=False, index=True),
    Column("reporter_id", String(64)),
    Column("name", String(40)),
    Column("text", Text, nullable=False),
    Column("is_bot", Boolean, nullable=False, default=False),
    Column("sim_min", Integer, nullable=False),
    Column("clock", String(5)),
    Column("flood_evidence", Float),
    Column("hazard", String(32)),
    Column("ai_engine", String(64)),
    Column("ai", JSONB),
    Column("lat", Float),
    Column("lng", Float),
    Column("photo", LargeBinary),
    Column("photo_mime", String(32)),
    Column("created_at", DateTime(timezone=True), nullable=False, default=_now),
    UniqueConstraint("run_id", "code"),
)

incidents = Table(
    "incidents", metadata,
    Column("id", Integer, primary_key=True),
    Column("run_id", UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("code", String(16), nullable=False),
    Column("sector", String(8), nullable=False, index=True),
    Column("status", String(16), nullable=False),
    Column("severity", String(16), nullable=False),
    Column("peak_severity", String(16), nullable=False),
    Column("score", Integer),
    Column("confidence", String(8)),
    Column("explanation", Text),
    Column("opened_sim_min", Integer),
    Column("opened_clock", String(5)),
    Column("resolved_sim_min", Integer),
    Column("plan", JSONB),
    Column("impact", JSONB),
    Column("updated_at", DateTime(timezone=True), nullable=False, default=_now, onupdate=_now),
    UniqueConstraint("run_id", "code"),
)

trail = Table(
    "incident_trail", metadata,
    Column("id", Integer, primary_key=True),
    Column("run_id", UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
    Column("incident_code", String(16), nullable=False),
    Column("sim_min", Integer),
    Column("clock", String(5)),
    Column("kind", String(16)),
    Column("text", Text),
    Column("snapshot", JSONB),
    Column("created_at", DateTime(timezone=True), nullable=False, default=_now),
)

tickets = Table(
    "tickets", metadata,
    Column("id", Integer, primary_key=True),
    Column("run_id", UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("code", String(24), nullable=False),
    Column("incident_code", String(16)),
    Column("sector", String(8)),
    Column("kind", String(16)),
    Column("team", String(64)),
    Column("priority", String(4)),
    Column("eta_min", Integer),
    Column("status", String(32)),
    Column("history", JSONB),
    Column("updated_at", DateTime(timezone=True), nullable=False, default=_now, onupdate=_now),
    UniqueConstraint("run_id", "code"),
)

broadcasts = Table(
    "broadcasts", metadata,
    Column("id", Integer, primary_key=True),
    Column("run_id", UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("code", String(16), nullable=False),
    Column("incident_code", String(16)),
    Column("kind", String(16)),
    Column("severity", String(16)),
    Column("sectors", ARRAY(String(8))),
    Column("message_en", Text),
    Column("message_ur", Text),
    Column("recipients", Integer),
    Column("delivered", Integer),
    Column("failed", Integer),
    Column("clock", String(5)),
    Column("created_at", DateTime(timezone=True), nullable=False, default=_now),
)

events = Table(
    "events", metadata,
    Column("id", Integer, primary_key=True),
    Column("run_id", UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("sim_min", Integer),
    Column("clock", String(5)),
    Column("type", String(16)),
    Column("sector", String(8)),
    Column("text", Text),
    Column("created_at", DateTime(timezone=True), nullable=False, default=_now),
)

# ---------------------------------------------------------------- accounts & audit
users = Table(
    "users", metadata,
    Column("id", Integer, primary_key=True),
    Column("email", String(254), nullable=False, unique=True),
    Column("name", String(80), nullable=False),
    Column("role", String(16), nullable=False),  # authority | admin
    Column("password_hash", String(100), nullable=False),
    Column("active", Boolean, nullable=False, default=True),
    Column("created_at", DateTime(timezone=True), nullable=False, default=_now),
    Column("password_changed_at", DateTime(timezone=True), nullable=False, default=_now),
    Column("last_login_at", DateTime(timezone=True)),
    CheckConstraint("role in ('authority', 'admin')", name="ck_users_role"),
)

sessions = Table(
    "sessions", metadata,
    Column("id", Integer, primary_key=True),
    Column("token_hash", String(64), nullable=False, unique=True),  # sha256 of the cookie value
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("created_at", DateTime(timezone=True), nullable=False, default=_now),
    Column("last_seen_at", DateTime(timezone=True), nullable=False, default=_now),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("ip", String(64)),
    Column("user_agent", String(200)),
)

audit_log = Table(
    "audit_log", metadata,
    Column("id", Integer, primary_key=True),
    Column("at", DateTime(timezone=True), nullable=False, default=_now, index=True),
    Column("user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
    Column("email", String(254)),
    Column("action", String(48), nullable=False),
    Column("detail", JSONB),
    Column("ip", String(64)),
)


class Store:
    """Write-behind PostgreSQL store. All public `save_*` methods are non-blocking."""

    def __init__(self, url: str | None, migrate: bool = True):
        self.enabled = False
        self.error: str | None = None
        self.run_id: uuid.UUID | None = None
        self._run_row = None
        self._q: queue.Queue = queue.Queue()
        if not url:
            return
        try:
            self.engine = create_engine(_normalize(url), pool_pre_ping=True, future=True)
            if migrate:
                from .migrate import upgrade
                upgrade(self.engine)
            self.enabled = True
        except Exception as e:  # noqa: BLE001 - any failure → in-memory mode
            self.error = str(e).splitlines()[0][:200]
            log.warning("PostgreSQL unavailable, running in memory: %s", self.error)
            return
        self._thread = threading.Thread(target=self._worker, name="nikaasi-db-writer", daemon=True)
        self._thread.start()

    # ------------------------------------------------------------- writer thread
    def _worker(self) -> None:
        while True:
            op = self._q.get()
            try:
                if op is None:
                    return
                with self.engine.begin() as conn:
                    conn.execute(op)
            except Exception:  # noqa: BLE001 - keep the writer alive
                log.exception("DB write failed")
            finally:
                self._q.task_done()

    def _put(self, stmt) -> None:
        if not (self.enabled and self.run_id):
            return
        if self._run_row is not None:  # create the run row lazily, on its first record
            self._q.put(self._run_row)
            self._run_row = None
        self._q.put(stmt)

    def put_global(self, stmt) -> None:
        """Queue a write that does not belong to a simulation run (e.g. audit log)."""
        if self.enabled:
            self._q.put(stmt)

    def flush(self, timeout: float = 10.0) -> None:
        """Block until queued writes are applied (used by tests / shutdown)."""
        if not self.enabled:
            return
        done = threading.Event()
        threading.Thread(target=lambda: (self._q.join(), done.set()), daemon=True).start()
        done.wait(timeout)

    def close(self) -> None:
        if self.enabled:
            self.flush()
            self._q.put(None)

    # ------------------------------------------------------------- writes
    def new_run(self, scenario: str | None = None) -> None:
        if not self.enabled:
            return
        self.run_id = uuid.uuid4()
        self._scenario = scenario
        self._run_row = runs.insert().values(id=self.run_id, scenario=scenario, started_at=_now())

    def set_scenario(self, name: str) -> None:
        if self._run_row is not None:  # not written yet: just change what will be inserted
            self._run_row = runs.insert().values(id=self.run_id, scenario=name, started_at=_now())
        else:
            self._put(runs.update().where(runs.c.id == self.run_id).values(scenario=name))

    def save_report(self, r: dict, photo: str | None) -> None:
        data, mime = None, None
        m = re.match(r"^data:([^;]+);base64,(.*)$", photo or "", re.DOTALL)
        if m:
            mime, data = m.group(1), base64.b64decode(m.group(2))
        pos = r.get("position") or {}
        self._put(insert(reports).values(
            run_id=self.run_id, code=r["id"], sector=r["sector"], reporter_id=r["reporterId"], name=r["name"],
            text=r["text"], is_bot=r["bot"], sim_min=r["t"], clock=r["clock"],
            flood_evidence=r["ai"].get("floodEvidence"), hazard=r["ai"].get("hazard"), ai_engine=r["ai"].get("engine"),
            ai=_snap(r["ai"]), lat=pos.get("lat"), lng=pos.get("lng"), photo=data, photo_mime=mime, created_at=_now(),
        ).on_conflict_do_nothing())

    def save_incident(self, inc: dict) -> None:
        a = (inc.get("assessment") or {}).get("final") or {}
        ex = inc.get("execution")
        impact = None
        if ex:  # metrics only — full link snapshots stay in memory
            impact = {"executedClock": ex["executedClock"], "by": ex["by"], "actionsDone": ex["actionsDone"],
                      "before": {k: ex["before"][k] for k in ("sectorTTI", "metrics", "score", "severity")},
                      "after": ex.get("after")}
        row = dict(
            run_id=self.run_id, code=inc["id"], sector=inc["sector"], status=inc["status"], severity=inc["severity"],
            peak_severity=inc["peakSeverity"], score=a.get("score"), confidence=a.get("confidence"),
            explanation=a.get("explanation"), opened_sim_min=inc["openedAt"], opened_clock=inc["openedClock"],
            resolved_sim_min=inc.get("resolvedAt"), plan=_snap(inc.get("plan")), impact=_snap(impact), updated_at=_now(),
        )
        stmt = insert(incidents).values(**row)
        self._put(stmt.on_conflict_do_update(index_elements=["run_id", "code"],
                                             set_={k: stmt.excluded[k] for k in row if k not in ("run_id", "code")}))

    def save_trail(self, inc: dict, entry: dict) -> None:
        self._put(trail.insert().values(run_id=self.run_id, incident_code=inc["id"], sim_min=entry["simMin"],
                                        clock=entry["clock"], kind=entry["kind"], text=entry["text"],
                                        snapshot=_snap(entry.get("snapshot")), created_at=_now()))

    def save_ticket(self, t: dict) -> None:
        row = dict(run_id=self.run_id, code=t["id"], incident_code=t["incidentId"], sector=t["sector"], kind=t["kind"],
                   team=t["team"], priority=t["priority"], eta_min=t["etaMin"], status=t["status"],
                   history=_snap(t["history"]), updated_at=_now())
        stmt = insert(tickets).values(**row)
        self._put(stmt.on_conflict_do_update(index_elements=["run_id", "code"],
                                             set_={k: stmt.excluded[k] for k in ("status", "history", "updated_at")}))

    def save_broadcast(self, b: dict) -> None:
        self._put(broadcasts.insert().values(
            run_id=self.run_id, code=b["id"], incident_code=b["incidentId"], kind=b["kind"], severity=b.get("severity"),
            sectors=list(b["sectors"]), message_en=b["message"]["en"], message_ur=b["message"]["ur"],
            recipients=b["recipients"], delivered=b["delivered"], failed=b["failed"], clock=b["clock"], created_at=_now()))

    def save_event(self, e: dict) -> None:
        self._put(events.insert().values(run_id=self.run_id, sim_min=e["simMin"], clock=e["clock"], type=e["type"],
                                         sector=e["sector"], text=e["text"], created_at=_now()))

    # ------------------------------------------------------------- retention
    def purge_personal_data(self, days: int) -> int:
        """Remove photos, exact GPS, names and device ids from reports older than `days` (text stays)."""
        if not self.enabled or days <= 0:
            return 0
        from sqlalchemy import or_, update
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        stmt = (update(reports).where(reports.c.created_at < cutoff,
                                      or_(reports.c.photo.isnot(None), reports.c.lat.isnot(None),
                                          reports.c.name.isnot(None), reports.c.reporter_id.isnot(None)))
                .values(photo=None, photo_mime=None, lat=None, lng=None, name=None, reporter_id=None))
        with self.engine.begin() as c:
            return c.execute(stmt).rowcount or 0

    def get_photo(self, code: str) -> str | None:
        """A report photo from the current run as a data URL (fallback when evicted from memory)."""
        if not self.enabled or not self.run_id:
            return None
        self.flush(2)
        with self.engine.connect() as c:
            row = c.execute(select(reports.c.photo, reports.c.photo_mime)
                            .where(reports.c.run_id == self.run_id, reports.c.code == code)).first()
        if not row or row.photo is None:
            return None
        return f"data:{row.photo_mime};base64,{base64.b64encode(row.photo).decode()}"

    # ------------------------------------------------------------- reads (blocking; call via asyncio.to_thread)
    def status(self) -> dict:
        out = {"enabled": self.enabled, "runId": str(self.run_id) if self.run_id else None, "error": self.error}
        if self.enabled:
            with self.engine.connect() as c:
                out["counts"] = {t.name: c.execute(select(func.count()).select_from(t)).scalar_one()
                                 for t in (runs, reports, incidents, trail, tickets, broadcasts, events, users, sessions, audit_log)}
        return out

    def list_runs(self, limit: int = 20) -> list[dict]:
        rc = select(func.count()).where(reports.c.run_id == runs.c.id).scalar_subquery()
        ic = select(func.count()).where(incidents.c.run_id == runs.c.id).scalar_subquery()
        q = (select(runs.c.id, runs.c.scenario, runs.c.started_at, rc.label("reports"), ic.label("incidents"))
             .order_by(runs.c.started_at.desc()).limit(limit))
        with self.engine.connect() as c:
            return [_row(r) for r in c.execute(q).mappings()]

    def list_incidents(self, run_id: str | None = None, limit: int = 50) -> list[dict]:
        cols = [incidents.c[k] for k in ("run_id", "code", "sector", "status", "severity", "peak_severity", "score",
                                         "confidence", "explanation", "opened_clock", "impact", "updated_at")]
        q = select(*cols).order_by(incidents.c.updated_at.desc()).limit(limit)
        if run_id:
            q = q.where(incidents.c.run_id == uuid.UUID(run_id))
        with self.engine.connect() as c:
            return [_row(r) for r in c.execute(q).mappings()]

    def list_reports(self, run_id: str | None = None, sector: str | None = None, limit: int = 100) -> list[dict]:
        cols = [reports.c[k] for k in ("run_id", "code", "sector", "name", "text", "is_bot", "clock", "flood_evidence",
                                       "hazard", "ai_engine", "created_at")]
        q = select(*cols, (reports.c.photo.isnot(None)).label("has_photo")).order_by(reports.c.created_at.desc()).limit(limit)
        if run_id:
            q = q.where(reports.c.run_id == uuid.UUID(run_id))
        if sector:
            q = q.where(reports.c.sector == sector)
        with self.engine.connect() as c:
            return [_row(r) for r in c.execute(q).mappings()]

    def incident_trail(self, run_id: str, code: str) -> list[dict]:
        q = (select(trail.c.sim_min, trail.c.clock, trail.c.kind, trail.c.text, trail.c.snapshot)
             .where(trail.c.run_id == uuid.UUID(run_id), trail.c.incident_code == code).order_by(trail.c.id))
        with self.engine.connect() as c:
            return [_row(r) for r in c.execute(q).mappings()]


def _row(m) -> dict:
    out = {}
    for k, v in dict(m).items():
        out[k] = str(v) if isinstance(v, uuid.UUID) else v.isoformat() if isinstance(v, datetime) else v
    return out


def _normalize(url: str) -> str:
    # accept plain postgres:// URLs and pin the installed psycopg2 driver
    return re.sub(r"^postgres(ql)?(\+\w+)?://", "postgresql+psycopg2://", url)
