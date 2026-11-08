"""
Nikaasi FastAPI backend.

    cd nikaasi/backend
    python -m uvicorn app.main:app --port 3000        # or: python run.py

Serves the same JSON + Server-Sent Events API as the Node prototype and the
static frontend in ../public. Interactive API docs at /docs.

Env: DATABASE_URL (PostgreSQL), GEMINI_API_KEY, GEMINI_MODEL, AUTHORITY_PIN (default 1122), TICK_MS
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]  # nikaasi/
PUBLIC = ROOT / "public"


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$", line)
        if m and m.group(1) not in os.environ:
            os.environ[m.group(1)] = m.group(2).strip("\"'")


load_dotenv(ROOT / ".env")

from .config import sector_from_latlng, sector_from_xy  # noqa: E402  (after .env is loaded)
from .db import Store  # noqa: E402
from .engine import Engine, HttpError  # noqa: E402


def pin() -> str:
    return os.environ.get("AUTHORITY_PIN", "1122")


# ---------------------------------------------------------------- schemas
class ReportIn(BaseModel):
    text: str = Field(default="", max_length=2000)
    sector: str | None = None
    name: str | None = Field(default=None, max_length=80)
    reporterId: str | None = Field(default=None, max_length=128)
    photo: str | None = Field(default=None, max_length=2_100_000)
    lat: float | None = None
    lng: float | None = None
    x: float | None = None
    y: float | None = None


class AuthIn(BaseModel):
    pin: str | int


class ScenarioIn(BaseModel):
    action: Literal["play", "reset", "pause", "resume", "step", "speed"]
    speed: float | None = None


class WeatherIn(BaseModel):
    sector: str
    alert: Literal["none", "yellow", "orange", "red", "clear"]
    rainMmHr: float | None = 0


class SettingsIn(BaseModel):
    autoExecute: bool | None = None


# ---------------------------------------------------------------- app
def create_app(engine: Engine | None = None) -> FastAPI:
    # All routes are `async def` on purpose: the engine and its tick loop live on the
    # event loop thread, so no handler may touch it from a worker thread.
    eng = engine or Engine(tick_ms=int(os.environ.get("TICK_MS", "1500")), store=Store(os.environ.get("DATABASE_URL")))
    store = eng.store

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        eng.stop()
        await asyncio.to_thread(store.close)

    app = FastAPI(title="Nikaasi API", version="1.0.0", lifespan=lifespan,
                  description="Flood signal-fusion and coordinated response simulator — Pani se pehle, plan.")
    app.state.engine = eng

    @app.exception_handler(HttpError)
    async def _http_error(_: Request, e: HttpError):
        return JSONResponse({"error": e.message}, status_code=e.status)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, e: RequestValidationError):
        first = e.errors()[0] if e.errors() else {}
        if first.get("type") == "json_invalid":
            return JSONResponse({"error": "Invalid JSON"}, status_code=400)
        where = ".".join(str(x) for x in first.get("loc", []) if x != "body")
        return JSONResponse({"error": f"Invalid {where or 'request'}: {first.get('msg', 'bad input')}"}, status_code=400)

    def is_authority(x_authority_pin: str | None) -> bool:
        return x_authority_pin == pin()

    async def require_authority(x_authority_pin: str | None = Header(default=None)) -> None:
        if not is_authority(x_authority_pin):
            raise HttpError(401, "Authority PIN required")

    # -------- public
    @app.get("/api/config", tags=["public"])
    async def config():
        w = eng.world
        links = [{k: l.get(k) for k in ("id", "a", "b", "name", "kind", "underpass", "sectors")} for l in w["links"]]
        return {"world": {"nodes": w["nodes"], "sectors": w["sectors"], "links": links, "depots": w["depots"], "size": w["size"]},
                "ai": eng.ai_status()}

    @app.get("/api/state", tags=["public"])
    async def state(x_authority_pin: str | None = Header(default=None)):
        return eng.state(authority=is_authority(x_authority_pin))

    @app.get("/api/stream", tags=["public"], response_class=StreamingResponse)
    async def stream(request: Request, pin_q: str | None = Query(default=None, alias="pin")):
        authority = pin_q == pin()
        queue = eng.subscribe()

        async def gen():
            try:
                yield f"data: {json.dumps(eng.state(authority=authority))}\n\n"
                while True:
                    try:
                        await asyncio.wait_for(queue.get(), timeout=20)
                    except asyncio.TimeoutError:
                        yield ": ka\n\n"
                        continue
                    if await request.is_disconnected():
                        break
                    yield f"data: {json.dumps(eng.state(authority=authority))}\n\n"
            finally:
                eng.unsubscribe(queue)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"cache-control": "no-cache", "x-accel-buffering": "no"})

    @app.get("/api/feeds/weather", tags=["mock feeds"])
    async def weather_feed():
        return {"source": "PMD Islamabad (mock feed)", "observedAt": eng.clock(), "sectors": eng.weather}

    @app.get("/api/feeds/traffic", tags=["mock feeds"])
    async def traffic_feed():
        return eng.traffic_feed()

    @app.post("/api/reports", tags=["citizen"])
    async def submit_report(body: ReportIn):
        sector = body.sector
        position = None
        if body.lat is not None and body.lng is not None:
            position = {"lat": body.lat, "lng": body.lng}
            sector = sector or sector_from_latlng(eng.world, body.lat, body.lng)
        elif body.x is not None and body.y is not None:
            position = {"x": body.x, "y": body.y}
            sector = sector or sector_from_xy(eng.world, body.x, body.y)
        return await eng.submit_report(sector=sector, text=body.text, reporter_id=body.reporterId, name=body.name,
                                       photo=body.photo, position=position)

    @app.get("/api/reports/{rid}/photo", tags=["citizen"])
    async def report_photo(rid: str):
        p = eng.photos.get(rid)
        m = re.match(r"^data:([^;]+);base64,(.*)$", p or "", re.DOTALL)
        if not m:
            raise HttpError(404, "No photo")
        return Response(base64.b64decode(m.group(2)), media_type=m.group(1), headers={"cache-control": "max-age=3600"})

    @app.get("/api/alerts", tags=["citizen"])
    async def alerts(sector: str | None = None):
        items = [b for b in eng.broadcasts if not sector or sector in b["sectors"]]
        return list(reversed(items[-20:]))

    @app.post("/api/auth", tags=["authority"])
    async def auth(body: AuthIn):
        if str(body.pin) != pin():
            raise HttpError(401, "Wrong PIN")
        return {"ok": True}

    # -------- authority
    @app.post("/api/scenario", tags=["authority"], dependencies=[Depends(require_authority)])
    async def scenario(body: ScenarioIn):
        if body.action == "play":
            eng.play_scenario()
        elif body.action == "reset":
            eng.reset()
        elif body.action == "pause":
            eng.stop()
        elif body.action == "resume":
            eng.start()
        elif body.action == "step":
            eng.tick()
        elif body.action == "speed":
            eng.set_speed(body.speed)
        eng.emit()
        return {"ok": True, "running": eng.running, "speed": eng.speed}

    @app.post("/api/weather", tags=["authority"], dependencies=[Depends(require_authority)])
    async def weather_override(body: WeatherIn):
        eng.set_weather_override(body.sector, body.alert, body.rainMmHr)
        return {"ok": True}

    @app.post("/api/settings", tags=["authority"], dependencies=[Depends(require_authority)])
    async def settings(body: SettingsIn):
        if body.autoExecute is not None:
            eng.settings["autoExecute"] = body.autoExecute
        eng.emit()
        return eng.settings

    @app.post("/api/incidents/{iid}/execute", tags=["authority"], dependencies=[Depends(require_authority)])
    async def execute(iid: str):
        inc = eng.execute(iid, "duty officer")
        return {"ok": True, "id": inc["id"], "status": inc["status"]}

    @app.post("/api/incidents/{iid}/reassess", tags=["authority"], dependencies=[Depends(require_authority)], status_code=202)
    async def reassess(iid: str):
        inc = next((i for i in eng.incidents if i["id"] == iid), None)
        if not inc:
            raise HttpError(404, "Not found")
        eng.request_ai(inc["sector"], "manual", force=True)
        return {"ok": True, "aiEnabled": eng.ai_status()["enabled"]}

    # -------- history (PostgreSQL)
    def require_db() -> None:
        if not store.enabled:
            raise HttpError(503, "Database not configured" + (f": {store.error}" if store.error else " (set DATABASE_URL)"))

    def valid_uuid(v: str | None) -> str | None:
        if v is None:
            return None
        try:
            return str(uuid.UUID(v))
        except ValueError:
            raise HttpError(400, "Invalid run id") from None

    @app.get("/api/db/status", tags=["history"], dependencies=[Depends(require_authority)])
    async def db_status():
        return await asyncio.to_thread(store.status)

    @app.get("/api/history/runs", tags=["history"], dependencies=[Depends(require_authority)])
    async def history_runs(limit: int = Query(default=20, ge=1, le=200)):
        require_db()
        return await asyncio.to_thread(store.list_runs, limit)

    @app.get("/api/history/incidents", tags=["history"], dependencies=[Depends(require_authority)])
    async def history_incidents(run: str | None = None, limit: int = Query(default=50, ge=1, le=500)):
        require_db()
        return await asyncio.to_thread(store.list_incidents, valid_uuid(run), limit)

    @app.get("/api/history/incidents/{run}/{code}/trail", tags=["history"], dependencies=[Depends(require_authority)])
    async def history_trail(run: str, code: str):
        require_db()
        return await asyncio.to_thread(store.incident_trail, valid_uuid(run), code)

    @app.get("/api/history/reports", tags=["history"], dependencies=[Depends(require_authority)])
    async def history_reports(run: str | None = None, sector: str | None = None, limit: int = Query(default=100, ge=1, le=1000)):
        require_db()
        return await asyncio.to_thread(store.list_reports, valid_uuid(run), sector, limit)

    @app.api_route("/api/{rest:path}", methods=["GET", "POST"], include_in_schema=False)
    async def api_404(rest: str):
        raise HttpError(404, "Not found")

    # -------- frontend
    if PUBLIC.exists():
        app.mount("/", StaticFiles(directory=PUBLIC, html=True), name="public")
    return app


app = create_app()
