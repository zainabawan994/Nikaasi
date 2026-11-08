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
  DOMAIN / ALLOWED_HOSTS   accepted Host headers (localhost and 127.0.0.1 always allowed)
  RATE_LIMIT_READ_PER_MIN / _WRITE_PER_MIN / _STATIC_PER_MIN / _STAFF_PER_MIN, MAX_STREAMS_PER_IP, MAX_STREAMS
  WEATHER_SOURCE     live (default: real rain from Open-Meteo, real-time clock) | demo (scripted story only)
  WEATHER_REFRESH_S  live rain refresh interval, default 300 · WEATHER_ALERT_MM  "10,25,50" mm/h for yellow/orange/red
  DATA_RETENTION_DAYS  personal data in reports (photo, GPS, name, device id) is removed after this (default 90, 0 = keep)
  GEMINI_API_KEY, GEMINI_MODEL, GEMINI_REPORT_TIMEOUT_S, TICK_MS
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .env import ROOT, load_dotenv

PUBLIC = ROOT / "public"
log = logging.getLogger("nikaasi")
load_dotenv()

from .auth import AuthError, AuthService  # noqa: E402  (after .env is loaded)
from .config import sector_from_latlng, sector_from_xy  # noqa: E402
from .db import Store  # noqa: E402
from .engine import Engine, HttpError  # noqa: E402
from .security import (ApiRateLimit, BodySizeLimit, CsrfOriginCheck, RateLimiter, SecurityHeaders,  # noqa: E402
                       allowed_hosts, client_ip, password_problem, validate_photo)


def production() -> bool:
    return os.environ.get("APP_ENV", "development").lower() == "production"


def cookie_secure() -> bool:
    v = os.environ.get("COOKIE_SECURE")
    return v == "1" if v is not None else production()


# ---------------------------------------------------------------- schemas
SECTOR = r"^[FGH]-(8|9|10|11)$"
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def clean_text(v: str | None) -> str | None:
    """Drop control characters (keeps newlines/tabs) and surrounding whitespace."""
    return None if v is None else CONTROL.sub("", v).strip()


class ReportIn(BaseModel):
    text: str = Field(default="", max_length=2000)
    sector: str | None = Field(default=None, pattern=SECTOR)
    name: str | None = Field(default=None, max_length=80)
    reporterId: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.:-]{1,128}$")
    photo: str | None = Field(default=None, max_length=2_100_000)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)
    x: float | None = Field(default=None, ge=0, le=2000)
    y: float | None = Field(default=None, ge=0, le=2000)

    _clean = field_validator("text", "name")(clean_text)


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
    confirmPassword: str = Field(max_length=256)  # the acting admin's own password (step-up check)

    _clean = field_validator("name")(clean_text)


class UserUpdateIn(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    role: Literal["authority", "admin"] | None = None
    active: bool | None = None
    password: str | None = Field(default=None, max_length=256)
    confirmPassword: str = Field(max_length=256)

    _clean = field_validator("name")(clean_text)


class ScenarioIn(BaseModel):
    action: Literal["play", "live", "reset", "pause", "resume", "step", "speed"]
    speed: float | None = Field(default=None, ge=0.5, le=8)


class WeatherIn(BaseModel):
    sector: str = Field(pattern=SECTOR)
    alert: Literal["none", "yellow", "orange", "red", "clear"]
    rainMmHr: float | None = Field(default=0, ge=0, le=150)


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
    reporter_lim = RateLimiter(5, 10 * 60)     # reports per reporter id per 10 min
    staff_writes = RateLimiter(int(os.environ.get("RATE_LIMIT_STAFF_PER_MIN", "30")), 60)  # per signed-in user
    ai_calls = RateLimiter(5, 60)              # manual Gemini re-judgements per user per minute (costs money)
    stream_limit = {"per_ip": int(os.environ.get("MAX_STREAMS_PER_IP", "6")), "total": int(os.environ.get("MAX_STREAMS", "300"))}
    streams: dict[str, int] = {}
    blocked_logged: dict[str, float] = {}

    def note_block(ip: str, bucket: str, path: str) -> None:
        """Audit rate-limit hits, at most once per IP and bucket every 5 minutes."""
        key, now = f"{ip}:{bucket}", time.monotonic()
        if now - blocked_logged.get(key, -1e9) > 300:
            blocked_logged[key] = now
            auth.audit(None, "ratelimit.blocked", {"bucket": bucket, "path": path[:80]}, ip)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await asyncio.to_thread(bootstrap_admin)
        if eng.default_mode == "live":
            eng.go_live()  # real-time clock + live rainfall from the start
        retention = asyncio.create_task(retention_loop())
        yield
        retention.cancel()
        eng.stop()
        await asyncio.to_thread(store.close)

    async def retention_loop() -> None:
        """Daily: strip photos, exact GPS, names and device ids from reports older than DATA_RETENTION_DAYS."""
        days = int(os.environ.get("DATA_RETENTION_DAYS", "90"))
        while True:
            try:
                n = await asyncio.to_thread(store.purge_personal_data, days)
                if n:
                    auth.audit(None, "retention.purge", {"reports": n, "olderThanDays": days})
            except Exception:  # noqa: BLE001 - retention must never take the app down
                log.exception("retention purge failed")
            await asyncio.sleep(24 * 3600)

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
    # outermost last: headers → host check → rate limit → size limit → CSRF → routes
    app.add_middleware(CsrfOriginCheck, allowed=allowed)
    app.add_middleware(BodySizeLimit, max_bytes=3_000_000)
    app.add_middleware(ApiRateLimit, on_block=note_block)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts())
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

    def limited(role: str = "authority"):
        """Signed-in user + per-user limit on state-changing staff actions."""
        base = require(role)

        async def dep(request: Request) -> dict:
            user = await base(request)
            ok, _, retry = staff_writes.take(f"user:{user['id']}")
            if not ok:
                note_block(client_ip(request), "staff", request.url.path)
                raise HttpError(429, f"Too many actions. Try again in {retry} s.")
            return user
        return dep

    staff_action = limited("authority")
    admin_action = limited("admin")

    async def confirm_admin(admin: dict, password: str, request: Request) -> None:
        """Step-up check: sensitive admin changes need the admin's own password again."""
        key = admin["email"]
        if login_fail.blocked(key):
            raise HttpError(429, "Too many failed confirmations. Try again in 15 minutes.")
        if not await asyncio.to_thread(auth.authenticate, admin["email"], password):
            login_fail.hit(key)
            audit(admin, "confirm.failed", None, request)
            raise HttpError(403, "Password confirmation failed")

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
        ip = client_ip(request)
        if streams.get(ip, 0) >= stream_limit["per_ip"] or sum(streams.values()) >= stream_limit["total"]:
            note_block(ip, "stream", "/api/stream")
            raise HttpError(429, "Too many open live connections")
        authority = bool(await current_user(request))
        token = request.cookies.get(cookie_name)
        queue = eng.subscribe()
        streams[ip] = streams.get(ip, 0) + 1

        async def gen():
            last_check = time.monotonic()
            try:
                yield f"data: {json.dumps(eng.state(authority=authority))}\n\n"
                while True:
                    try:
                        await asyncio.wait_for(queue.get(), timeout=20)
                    except asyncio.TimeoutError:
                        yield ": ka\n\n"
                    if await request.is_disconnected():
                        break
                    # a staff stream ends as soon as the session is revoked or expires
                    if authority and time.monotonic() - last_check >= 30:
                        last_check = time.monotonic()
                        if not await asyncio.to_thread(auth.session_user, token, False):
                            break
                    yield f"data: {json.dumps(eng.state(authority=authority))}\n\n"
            finally:
                eng.unsubscribe(queue)
                streams[ip] = max(0, streams.get(ip, 1) - 1)
                if not streams[ip]:
                    streams.pop(ip, None)

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
        if body.reporterId and not reporter_lim.hit(body.reporterId):
            raise HttpError(429, "You have sent several reports already. Thank you; please wait a few minutes.")
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
                              user: dict = Depends(staff_action)):
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
    async def create_user(body: UserCreateIn, request: Request, admin: dict = Depends(admin_action)):
        await confirm_admin(admin, body.confirmPassword, request)
        if problem := password_problem(body.password):
            raise HttpError(400, problem)
        u = await asyncio.to_thread(auth.create_user, body.email, body.name, body.role, body.password)
        audit(admin, "user.created", {"id": u["id"], "email": u["email"], "role": u["role"]}, request)
        return u

    @app.patch("/api/users/{user_id}", tags=["admin"])
    async def update_user(user_id: int, body: UserUpdateIn, request: Request, admin: dict = Depends(admin_action)):
        await confirm_admin(admin, body.confirmPassword, request)
        if user_id == admin["id"] and (body.active is False or (body.role is not None and body.role != "admin")):
            raise HttpError(409, "You can't remove your own admin access. Ask another admin to do it.")
        if body.password is not None and (problem := password_problem(body.password)):
            raise HttpError(400, problem)
        u = await asyncio.to_thread(auth.update_user, user_id, name=body.name, role=body.role, active=body.active,
                                    password=body.password)
        changed = {k: v for k, v in body.model_dump(exclude_none=True).items() if k not in ("password", "confirmPassword")}
        if body.password is not None:
            changed["password"] = "reset"
        audit(admin, "user.updated", {"id": user_id, **changed}, request)
        return u

    @app.get("/api/audit", tags=["admin"])
    async def audit_log(limit: int = Query(default=100, ge=1, le=1000), _: dict = Depends(require_admin)):
        return await asyncio.to_thread(auth.list_audit, limit)

    # -------- authority: operations
    @app.get("/api/reports/{rid}/photo", tags=["authority"])
    async def report_photo(rid: str = Path(pattern=r"^R-\d{1,6}$"), _: dict = Depends(require_authority)):
        p = eng.photos.get(rid) or await asyncio.to_thread(store.get_photo, rid)  # older photos come from PostgreSQL
        m = re.match(r"^data:([^;]+);base64,(.*)$", p or "", re.DOTALL)
        if not m:
            raise HttpError(404, "No photo")
        return Response(base64.b64decode(m.group(2)), media_type=m.group(1),
                        headers={"cache-control": "private, max-age=600", "content-disposition": "inline"})

    @app.post("/api/scenario", tags=["authority"])
    async def scenario(body: ScenarioIn, request: Request, user: dict = Depends(staff_action)):
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
    async def weather_override(body: WeatherIn, request: Request, user: dict = Depends(staff_action)):
        eng.set_weather_override(body.sector, body.alert, body.rainMmHr, by=user["name"])
        audit(user, "weather.pmd_alert" if eng.mode == "live" else "weather.override",
              {k: v for k, v in body.model_dump().items() if eng.mode != "live" or k != "rainMmHr"}, request)
        return {"ok": True}

    @app.post("/api/settings", tags=["authority"])
    async def settings(body: SettingsIn, request: Request, user: dict = Depends(staff_action)):
        if body.autoExecute is not None:
            eng.settings["autoExecute"] = body.autoExecute
            audit(user, "settings.autoExecute", {"value": body.autoExecute}, request)
        eng.emit()
        return eng.settings

    @app.post("/api/incidents/{iid}/execute", tags=["authority"])
    async def execute(iid: str, request: Request, user: dict = Depends(staff_action)):
        inc = eng.execute(iid, user["name"])
        audit(user, "incident.execute", {"incident": inc["id"], "sector": inc["sector"], "plan": inc["plan"]["version"]}, request)
        return {"ok": True, "id": inc["id"], "status": inc["status"]}

    @app.post("/api/incidents/{iid}/reassess", tags=["authority"], status_code=202)
    async def reassess(iid: str, request: Request, user: dict = Depends(staff_action)):
        ok, _, retry = ai_calls.take(f"user:{user['id']}")
        if not ok:
            raise HttpError(429, f"Gemini was asked recently. Try again in {retry} s.")
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

    # -------- staff pages: never served to signed-out visitors
    NOINDEX = {"X-Robots-Tag": "noindex, nofollow", "Cache-Control": "no-store"}

    async def staff_page(request: Request, name: str, admin: bool = False):
        user = await current_user(request)
        if not user:
            return RedirectResponse(f"/login.html?next=/{name}", status_code=303, headers=NOINDEX)
        if admin and user["role"] != "admin":
            return RedirectResponse("/authority.html", status_code=303, headers=NOINDEX)
        return FileResponse(PUBLIC / name, media_type="text/html", headers=NOINDEX)

    @app.get("/authority.html", include_in_schema=False)
    @app.get("/authority", include_in_schema=False)
    async def authority_page(request: Request):
        return await staff_page(request, "authority.html")

    @app.get("/admin.html", include_in_schema=False)
    @app.get("/admin", include_in_schema=False)
    async def admin_page(request: Request):
        return await staff_page(request, "admin.html", admin=True)

    @app.get("/login.html", include_in_schema=False)
    @app.get("/login", include_in_schema=False)
    async def login_page():
        return FileResponse(PUBLIC / "login.html", media_type="text/html", headers=NOINDEX)

    # -------- frontend
    if PUBLIC.exists():
        app.mount("/", StaticFiles(directory=PUBLIC, html=True), name="public")
    return app


app = create_app()
