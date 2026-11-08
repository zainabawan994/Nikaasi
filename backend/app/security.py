"""
Security helpers: password hashing, session tokens, rate limiting, upload
validation and HTTP hardening middleware.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from urllib.parse import urlparse

import bcrypt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

# ------------------------------------------------------------------ passwords
MIN_PASSWORD_LEN = 10


def hash_password(password: str) -> str:
    # bcrypt only uses the first 72 bytes; pre-hash so long passphrases are not truncated
    return bcrypt.hashpw(_prehash(password), bcrypt.gensalt(rounds=12)).decode()


_DUMMY_HASH = bcrypt.hashpw(b"timing-equaliser", bcrypt.gensalt(rounds=12))


def verify_password(password: str, hashed: str | None) -> bool:
    if not hashed:
        # same work for unknown users as for wrong passwords (no user enumeration by timing)
        bcrypt.checkpw(b"x", _DUMMY_HASH)
        return False
    try:
        return bcrypt.checkpw(_prehash(password), hashed.encode())
    except ValueError:
        return False


def _prehash(password: str) -> bytes:
    return base64.b64encode(hashlib.sha256(password.encode("utf-8")).digest())


def password_problem(password: str) -> str | None:
    if len(password) < MIN_PASSWORD_LEN:
        return f"Password must be at least {MIN_PASSWORD_LEN} characters"
    if password.lower() in {"password123", "nikaasi1234", "1234567890", "qwertyuiop"}:
        return "Password is too common"
    if not (re.search(r"[A-Za-z]", password) and re.search(r"[0-9\W_]", password)):
        return "Use letters and at least one number or symbol"
    return None


# ------------------------------------------------------------------ session tokens
def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    """Only the SHA-256 of a session token is stored, so a DB leak can't be replayed."""
    return hashlib.sha256(token.encode()).hexdigest()


def same_secret(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


# ------------------------------------------------------------------ rate limiting
class RateLimiter:
    """In-process sliding-window limiter (one instance per app; fine for a single worker)."""

    def __init__(self, limit: int, window_s: float):
        self.limit, self.window = limit, window_s
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def _trim(self, q: deque, now: float) -> None:
        while q and now - q[0] > self.window:
            q.popleft()

    def hit(self, key: str) -> bool:
        """Record a hit; return False when the key is over its limit."""
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            self._trim(q, now)
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True

    def blocked(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            self._trim(q, now)
            return len(q) >= self.limit

    def retry_after(self, key: str) -> int:
        with self._lock:
            q = self._hits.get(key)
            return max(1, int(self.window - (time.monotonic() - q[0]))) if q else 1

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)


def client_ip(request: Request) -> str:
    # Only trust X-Forwarded-For when explicitly running behind a proxy we control
    if os.environ.get("TRUST_PROXY") == "1":
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ------------------------------------------------------------------ uploads
MAX_PHOTO_BYTES = 1_500_000
_MAGIC = {
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/webp": (b"RIFF",),
}


def validate_photo(data_url: str | None) -> str | None:
    """Return a clean data URL or None; raise ValueError for anything suspicious."""
    if not data_url:
        return None
    m = re.match(r"^data:(image/(?:jpeg|png|webp));base64,([A-Za-z0-9+/=\s]+)$", data_url)
    if not m:
        raise ValueError("Photo must be a JPEG, PNG or WebP image")
    mime, b64 = m.group(1), re.sub(r"\s", "", m.group(2))
    try:
        raw = base64.b64decode(b64, validate=True)
    except ValueError:
        raise ValueError("Photo is not valid base64") from None
    if len(raw) > MAX_PHOTO_BYTES:
        raise ValueError("Photo too large (max 1.5 MB)")
    if not raw.startswith(_MAGIC[mime]) or (mime == "image/webp" and raw[8:12] != b"WEBP"):
        raise ValueError("Photo content does not match its type")
    return f"data:{mime};base64,{b64}"


# ------------------------------------------------------------------ middleware
CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com",
    "img-src 'self' data: blob:",
    "connect-src 'self'",
    "frame-ancestors 'none'",
    "base-uri 'none'",
    "form-action 'self'",
    "object-src 'none'",
])
DOCS_CSP = CSP.replace("script-src 'self'", "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net") \
              .replace("style-src 'self' 'unsafe-inline'", "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net") \
              .replace("img-src 'self' data: blob:", "img-src 'self' data: blob: https://fastapi.tiangolo.com")


class SecurityHeaders(BaseHTTPMiddleware):
    def __init__(self, app, hsts: bool = False):
        super().__init__(app)
        self.hsts = hsts

    async def dispatch(self, request: Request, call_next) -> Response:
        resp = await call_next(request)
        path = request.url.path
        h = resp.headers
        h.setdefault("Content-Security-Policy", DOCS_CSP if path in ("/docs", "/redoc") else CSP)
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Referrer-Policy", "same-origin")
        h.setdefault("Permissions-Policy", "geolocation=(self), camera=(self), microphone=(), payment=()")
        h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        if path.startswith("/api/"):
            h.setdefault("Cache-Control", "no-store")
        elif path == "/" or path.endswith((".html", ".js", ".css", ".svg")):
            # always revalidate (cheap 304 via ETag) so users get updates immediately
            h.setdefault("Cache-Control", "no-cache")
        if self.hsts:
            h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return resp


class CsrfOriginCheck(BaseHTTPMiddleware):
    """
    Cookie-authenticated API → reject state-changing requests from other sites.
    Browsers always send Origin on cross-site POST/PUT/PATCH/DELETE; it must match
    this host (or ALLOWED_ORIGINS). Non-browser clients without Origin/Referer pass.
    """

    def __init__(self, app, allowed: set[str]):
        super().__init__(app)
        self.allowed = allowed

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.url.path.startswith("/api/"):
            origin = request.headers.get("origin") or request.headers.get("referer")
            if origin:
                o = urlparse(origin)
                origin_host = f"{o.scheme}://{o.netloc}"
                host = request.headers.get("host", "")
                same = o.netloc == host
                if not same and origin_host not in self.allowed:
                    return JSONResponse({"error": "Cross-site request blocked"}, status_code=403)
        return await call_next(request)


class BodySizeLimit(BaseHTTPMiddleware):
    def __init__(self, app, max_bytes: int):
        super().__init__(app)
        self.max = max_bytes

    async def dispatch(self, request: Request, call_next) -> Response:
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > self.max:
            return JSONResponse({"error": "Payload too large"}, status_code=413)
        return await call_next(request)
