"""
Nikaasi FastAPI backend (full stack: API + SSE + static frontend + PostgreSQL).

    cd nikaasi/backend
    python run.py                     # or: python -m uvicorn app.main:app --port 3000

Environment (see .env.example and SECURITY.md):
  APP_ENV            development | production  (production: no /docs, secure cookies, HSTS)
  DATABASE_URL       PostgreSQL URL; without it everything is kept in memory
  ADMIN_EMAIL / ADMIN_PASSWORD   bootstrap the first admin when no users exist
  ALLOWED_ORIGINS    extra origins allowed to POST (comma separated); same-origin always allowed
  COOKIE_SECURE      1 to force Secure cookies (default: on in production)
  TRUST_PROXY        1 when behind a reverse proxy that sets X-Forwarded-For
  WEATHER_SOURCE     live (default: real rain from Open-Meteo, real-time clock) | demo (scripted story only)
  WEATHER_REFRESH_S  live rain refresh interval, default 300 · WEATHER_ALERT_MM  "10,25,50" mm/h for yellow/orange/red
  GEMINI_API_KEY, GEMINI_MODEL, TICK_MS
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import uuid
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .env import ROOT, load_dotenv

PUBLIC = ROOT / "public"
log = logging.getLogger("nikaasi")
load_dotenv()

from .auth import AuthError, AuthService  # noqa: E402  (after .env is loaded)
from .config import sector_from_latlng, sector_from_xy  # noqa: E402
from .db import Store  # noqa: E402
from .engine import Engine, HttpError  # noqa: E402
from .security import (BodySizeLimit, CsrfOriginCheck, RateLimiter, SecurityHeaders, client_ip,  # noqa: E402
                       password_problem, validate_photo)


def production() -> bool:
    return os.environ.get("APP_ENV", "development").lower() == "production"


def cookie_secure() -> bool:
    v = os.environ.get("COOKIE_SECURE")
    return v == "1" if v is not None else production()


# ---------------------------------------------------------------- schemas
class ReportIn(BaseModel):
    text: str = Field(default="", max_length=2000)
    sector: str | None = Field(default=None, max_length=8)
    name: str | None = Field(default=None, max_length=80)
    reporterId: str | None = Field(default=None, max_length=128)
    photo: str | None = Field(default=None, max_length=2_100_000)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)
    x: float | None = None
    y: float | None = None


class LoginIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=256)


class PasswordChangeIn(BaseModel):
    current: str = Field(max_length=256)
    new: str = Field(max_length=256)


class UserCreateIn(BaseModel):
    email: str = Field(max_length=254)
    name: str = Field(default="", max_length=80)
    role: Literal["authority", "admin"] = "authority"
    password: str = Field(max_length=256)


class UserUpdateIn(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    role: Literal["authority", "admin"] | None = None
    active: bool | None = None
    password: str | None = Field(default=None, max_length=256)


class ScenarioIn(BaseModel):
    action: Literal["play", "live", "reset", "pause", "resume", "step", "speed"]
    speed: float | None = None


class WeatherIn(BaseModel):
    sector: str = Field(max_length=8)
    alert: Literal["none", "yellow", "orange", "red", "clear"]
    rainMmHr: float | None = 0


class SettingsIn(BaseModel):
    autoExecute: bool | None = None


# ---------------------------------------------------------------- app
def create_app(engine: Engine | None = None) -> FastAPI:
    # All routes are `async def` on purpose: the engine and its tick loop live on the
    # event loop thread, so no handler may touch it from a worker thread.
    eng = engine or Engine(tick_ms=int(os.environ.get("TICK_MS", "1500")), store=Store(os.environ.get("DATABASE_URL")),
                           weather_source=os.environ.get("WEATHER_SOURCE", "live"))
    store = eng.store
    auth = AuthService(store)
    cookie_name = "__Host-nikaasi_session" if cookie_secure() else "nikaasi_session"

    login_ip = RateLimiter(20, 15 * 60)        # attempts per IP per 15 min
    login_fail = RateLimiter(5, 15 * 60)       # failures per account per 15 min
    report_min = RateLimiter(10, 60)           # citizen reports per IP per minute
    report_hour = RateLimiter(60, 60 * 60)     # ... and per hour

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await asyncio.to_thread(bootstrap_admin)
        if eng.default_mode == "live":
            eng.go_live()  # real-time clock + live rainfall from the start
        yield
        eng.stop()
        await asyncio.to_thread(store.close)

    def bootstrap_admin() -> None:
        email, pw = os.environ.get("ADMIN_EMAIL"), os.environ.get("ADMIN_PASSWORD")
        if not (email and pw) or auth.count_users() > 0:
            if auth.count_users() == 0:
                log.warning("No users exist. Set ADMIN_EMAIL and ADMIN_PASSWORD, or run: python -m app.cli create-user")
            return
        if problem := password_problem(pw):
            log.error("ADMIN_PASSWORD rejected: %s", problem)
            return
        u = auth.create_user(email, "Administrator", "admin", pw)
        auth.audit(None, "user.bootstrap", {"email": u["email"]})
        log.warning("Created first admin %s from ADMIN_EMAIL / ADMIN_PASSWORD", u["email"])

    app = FastAPI(title="Nikaasi API", version="2.0.0", lifespan=lifespan,
                  description="Flood signal-fusion and coordinated response simulator — Pani se pehle, plan.",
                  docs_url=None if production() else "/docs", redoc_url=None, openapi_url=None if production() else "/openapi.json")
    app.state.engine = eng
    app.state.auth = auth
    allowed = {o.strip().rstrip("/") for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()}
    app.add_middleware(CsrfOriginCheck, allowed=allowed)
    app.add_middleware(BodySizeLimit, max_bytes=3_000_000)
    app.add_middleware(SecurityHeaders, hsts=cookie_secure())

    @app.exception_handler(HttpError)
    async def _http_error(_: Request, e: HttpError):
        return JSONResponse({"error": e.message}, status_code=e.status)

    @app.exception_handler(AuthError)
    async def _auth_error(_: Request, e: AuthError):
        return JSONResponse({"error": e.message}, status_code=e.status)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, e: RequestValidationError):
        first = e.errors()[0] if e.errors() else {}
        if first.get("type") == "json_invalid":
            return JSONResponse({"error": "Invalid JSON"}, status_code=400)
        where = ".".join(str(x) for x in first.get("loc", []) if x != "body")
        return JSONResponse({"error": f"Invalid {where or 'request'}: {first.get('msg', 'bad input')}"}, status_code=400)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, e: Exception):
        log.exception("Unhandled error")
        return JSONResponse({"error": "Internal error"}, status_code=500)

    # -------- auth dependencies
    async def current_user(request: Request) -> dict | None:
        if not hasattr(request.state, "user"):  # resolve the session once per request
            request.state.user = await asyncio.to_thread(auth.session_user, request.cookies.get(cookie_name))
        return request.state.user

    def require(role: str = "authority"):
        async def dep(request: Request) -> dict:
            user = await current_user(request)
            if not user:
                raise HttpError(401, "Please sign in")
            if role == "admin" and user["role"] != "admin":
                raise HttpError(403, "Admin only")
            return user
        return dep

    require_authority = require("authority")
    require_admin = require("admin")

    def audit(user, action, detail=None, request: Request | None = None):
        auth.audit(user, action, detail, client_ip(request) if request else None)

    # -------- health
    @app.get("/api/health", tags=["public"])
    async def health():
        return {"status": "ok", "database": "connected" if store.enabled else ("error" if store.error else "disabled"),
                "ai": "gemini" if eng.ai_status()["enabled"] else "offline"}

    # -------- public
    @app.get("/api/config", tags=["public"])
    async def config():
        w = eng.world
        links = [{k: l.get(k) for k in ("id", "a", "b", "name", "kind", "underpass", "sectors")} for l in w["links"]]
        return {"world": {"nodes": w["nodes"], "sectors": w["sectors"], "links": links, "depots": w["depots"], "size": w["size"]},
                "ai": eng.ai_status()}

    @app.get("/api/state", tags=["public"])
    async def state(request: Request):
        return eng.state(authority=bool(await current_user(request)))

    @app.get("/api/stream", tags=["public"], response_class=StreamingResponse)
    async def stream(request: Request):
        authority = bool(await current_user(request))
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
    async def submit_report(body: ReportIn, request: Request):
        ip = client_ip(request)
        if not report_min.hit(ip) or not report_hour.hit(ip):
            raise HttpError(429, "Too many reports from this device. Please wait a few minutes.")
        try:
            photo = validate_photo(body.photo)
        except ValueError as e:
            raise HttpError(400, str(e)) from None
        sector = body.sector
        position = None
        if body.lat is not None and body.lng is not None:
            position = {"lat": body.lat, "lng": body.lng}
            sector = sector or sector_from_latlng(eng.world, body.lat, body.lng)
        elif body.x is not None and body.y is not None:
            position = {"x": body.x, "y": body.y}
            sector = sector or sector_from_xy(eng.world, body.x, body.y)
        return await eng.submit_report(sector=sector, text=body.text, reporter_id=body.reporterId, name=body.name,
                                       photo=photo, position=position)

    @app.get("/api/alerts", tags=["citizen"])
    async def alerts(sector: str | None = None):
        items = [b for b in eng.broadcasts if not sector or sector in b["sectors"]]
        return list(reversed(items[-20:]))

    # -------- authentication
    @app.post("/api/auth/login", tags=["auth"])
    async def login(body: LoginIn, request: Request, response: Response):
        ip = client_ip(request)
        key = body.email.strip().lower()
        if not login_ip.hit(ip) or login_fail.blocked(key):
            audit(None, "login.blocked", {"email": key}, request)
            raise HttpError(429, "Too many sign-in attempts. Try again in 15 minutes.")
        user = await asyncio.to_thread(auth.authenticate, body.email, body.password)
        if not user:
            login_fail.hit(key)
            audit(None, "login.failed", {"email": key}, request)
            raise HttpError(401, "Wrong email or password")
        login_fail.reset(key)
        token = await asyncio.to_thread(auth.create_session, user, ip, request.headers.get("user-agent", ""))
        response.set_cookie(cookie_name, token, httponly=True, secure=cookie_secure(), samesite="strict", path="/",
                            max_age=12 * 3600)
        audit(user, "login.ok", None, request)
        return {"id": user["id"], "email": user["email"], "name": user["name"], "role": user["role"]}

    @app.post("/api/auth/logout", tags=["auth"])
    async def logout(request: Request, response: Response):
        user = await current_user(request)
        await asyncio.to_thread(auth.end_session, request.cookies.get(cookie_name))
        response.delete_cookie(cookie_name, path="/", secure=cookie_secure(), httponly=True, samesite="strict")
        if user:
            audit(user, "logout", None, request)
        return {"ok": True}

    @app.get("/api/auth/me", tags=["auth"])
    async def me(user: dict = Depends(require_authority)):
        return user

    @app.post("/api/auth/password", tags=["auth"])
    async def change_password(body: PasswordChangeIn, request: Request, response: Response,
                              user: dict = Depends(require_authority)):
        if problem := password_problem(body.new):
            raise HttpError(400, problem)
        await asyncio.to_thread(auth.change_password, user["id"], body.current, body.new)
        # all sessions were ended: sign this browser in again
        token = await asyncio.to_thread(auth.create_session, user, client_ip(request), request.headers.get("user-agent", ""))
        response.set_cookie(cookie_name, token, httponly=True, secure=cookie_secure(), samesite="strict", path="/",
                            max_age=12 * 3600)
        audit(user, "password.changed", None, request)
        return {"ok": True}

    # -------- admin: users & audit
    @app.get("/api/users", tags=["admin"])
    async def list_users(_: dict = Depends(require_admin)):
        return await asyncio.to_thread(auth.list_users)

    @app.post("/api/users", tags=["admin"], status_code=201)
    async def create_user(body: UserCreateIn, request: Request, admin: dict = Depends(require_admin)):
        if problem := password_problem(body.password):
            raise HttpError(400, problem)
        u = await asyncio.to_thread(auth.create_user, body.email, body.name, body.role, body.password)
        audit(admin, "user.created", {"id": u["id"], "email": u["email"], "role": u["role"]}, request)
        return u

    @app.patch("/api/users/{user_id}", tags=["admin"])
    async def update_user(user_id: int, body: UserUpdateIn, request: Request, admin: dict = Depends(require_admin)):
        if body.password is not None and (problem := password_problem(body.password)):
            raise HttpError(400, problem)
        u = await asyncio.to_thread(auth.update_user, user_id, name=body.name, role=body.role, active=body.active,
                                    password=body.password)
        changed = {k: v for k, v in body.model_dump(exclude_none=True).items() if k != "password"}
        if body.password is not None:
            changed["password"] = "reset"
        audit(admin, "user.updated", {"id": user_id, **changed}, request)
        return u

    @app.get("/api/audit", tags=["admin"])
    async def audit_log(limit: int = Query(default=100, ge=1, le=1000), _: dict = Depends(require_admin)):
        return await asyncio.to_thread(auth.list_audit, limit)

    # -------- authority: operations
    @app.get("/api/reports/{rid}/photo", tags=["authority"])
    async def report_photo(rid: str, _: dict = Depends(require_authority)):
        p = eng.photos.get(rid)
        m = re.match(r"^data:([^;]+);base64,(.*)$", p or "", re.DOTALL)
        if not m:
            raise HttpError(404, "No photo")
        return Response(base64.b64decode(m.group(2)), media_type=m.group(1),
                        headers={"cache-control": "private, max-age=600", "content-disposition": "inline"})

    @app.post("/api/scenario", tags=["authority"])
    async def scenario(body: ScenarioIn, request: Request, user: dict = Depends(require_authority)):
        if body.action == "play":
            eng.play_scenario()
        elif body.action == "live":
            eng.go_live()
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
        if body.action in ("play", "live", "reset"):
            audit(user, f"scenario.{body.action}", None, request)
        eng.emit()
        return {"ok": True, "running": eng.running, "speed": eng.speed}

    @app.post("/api/weather", tags=["authority"])
    async def weather_override(body: WeatherIn, request: Request, user: dict = Depends(require_authority)):
        eng.set_weather_override(body.sector, body.alert, body.rainMmHr, by=user["name"])
        audit(user, "weather.pmd_alert" if eng.mode == "live" else "weather.override",
              {k: v for k, v in body.model_dump().items() if eng.mode != "live" or k != "rainMmHr"}, request)
        return {"ok": True}

    @app.post("/api/settings", tags=["authority"])
    async def settings(body: SettingsIn, request: Request, user: dict = Depends(require_authority)):
        if body.autoExecute is not None:
            eng.settings["autoExecute"] = body.autoExecute
            audit(user, "settings.autoExecute", {"value": body.autoExecute}, request)
        eng.emit()
        return eng.settings

    @app.post("/api/incidents/{iid}/execute", tags=["authority"])
    async def execute(iid: str, request: Request, user: dict = Depends(require_authority)):
        inc = eng.execute(iid, user["name"])
        audit(user, "incident.execute", {"incident": inc["id"], "sector": inc["sector"], "plan": inc["plan"]["version"]}, request)
        return {"ok": True, "id": inc["id"], "status": inc["status"]}

    @app.post("/api/incidents/{iid}/reassess", tags=["authority"], status_code=202)
    async def reassess(iid: str, _: dict = Depends(require_authority)):
        inc = next((i for i in eng.incidents if i["id"] == iid), None)
        if not inc:
            raise HttpError(404, "Not found")
        eng.request_ai(inc["sector"], "manual", force=True)
        return {"ok": True, "aiEnabled": eng.ai_status()["enabled"]}

    # -------- history (PostgreSQL)
    def require_db() -> None:
        if not store.enabled:
            raise HttpError(503, "Database not configured" + (": connection failed" if store.error else " (set DATABASE_URL)"))

    def valid_uuid(v: str | None) -> str | None:
        if v is None:
            return None
        try:
            return str(uuid.UUID(v))
        except ValueError:
            raise HttpError(400, "Invalid run id") from None

    @app.get("/api/db/status", tags=["history"])
    async def db_status(_: dict = Depends(require_admin)):
        s = await asyncio.to_thread(store.status)
        if s.get("error"):
            s["error"] = "connection failed"  # details go to the server log, not the browser
        return s

    @app.get("/api/history/runs", tags=["history"])
    async def history_runs(limit: int = Query(default=20, ge=1, le=200), _: dict = Depends(require_authority)):
        require_db()
        return await asyncio.to_thread(store.list_runs, limit)

    @app.get("/api/history/incidents", tags=["history"])
    async def history_incidents(run: str | None = None, limit: int = Query(default=50, ge=1, le=500),
                                _: dict = Depends(require_authority)):
        require_db()
        return await asyncio.to_thread(store.list_incidents, valid_uuid(run), limit)

    @app.get("/api/history/incidents/{run}/{code}/trail", tags=["history"])
    async def history_trail(run: str, code: str, _: dict = Depends(require_authority)):
        require_db()
        return await asyncio.to_thread(store.incident_trail, valid_uuid(run), code)

    @app.get("/api/history/reports", tags=["history"])
    async def history_reports(run: str | None = None, sector: str | None = None,
                              limit: int = Query(default=100, ge=1, le=1000), _: dict = Depends(require_authority)):
        require_db()
        return await asyncio.to_thread(store.list_reports, valid_uuid(run), sector, limit)

    @app.api_route("/api/{rest:path}", methods=["GET", "POST", "PATCH", "PUT", "DELETE"], include_in_schema=False)
    async def api_404(rest: str):
        raise HttpError(404, "Not found")

    # -------- frontend
    if PUBLIC.exists():
        app.mount("/", StaticFiles(directory=PUBLIC, html=True), name="public")
    return app


app = create_app()
