"""
Accounts, login sessions and audit logging.

Users and sessions live in PostgreSQL when the database is enabled, otherwise
in memory (useful for tests and quick demos without a database). All methods
are synchronous; call them from async routes with asyncio.to_thread.
"""
from __future__ import annotations

import itertools
import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, insert, select, update

from . import db
from .security import hash_password, new_token, token_hash, verify_password

ROLES = ("authority", "admin")
SESSION_IDLE = timedelta(hours=2)       # sign out after 2 h without activity
SESSION_ABSOLUTE = timedelta(hours=12)  # and always after 12 h
_PUBLIC = ("id", "email", "name", "role", "active", "created_at", "last_login_at")


class AuthError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _public(u: dict) -> dict:
    return {k: (u[k].isoformat() if isinstance(u.get(k), datetime) else u.get(k)) for k in _PUBLIC}


def normalize_email(email: str) -> str:
    e = (email or "").strip().lower()
    if not (3 <= len(e) <= 254) or e.count("@") != 1 or e.startswith("@") or e.endswith("@"):
        raise AuthError(400, "Enter a valid email address")
    return e


class AuthService:
    def __init__(self, store: db.Store):
        self.store = store
        self.db = store.enabled
        # in-memory backend
        self._lock = threading.Lock()
        self._users: dict[int, dict] = {}
        self._sessions: dict[str, dict] = {}
        self._audit: list[dict] = []
        self._ids = itertools.count(1)

    # ------------------------------------------------------------ users
    def count_users(self) -> int:
        if self.db:
            with self.store.engine.connect() as c:
                return c.execute(select(func.count()).select_from(db.users)).scalar_one()
        return len(self._users)

    def create_user(self, email: str, name: str, role: str, password: str) -> dict:
        email = normalize_email(email)
        name = (name or "").strip()[:80] or email.split("@")[0]
        if role not in ROLES:
            raise AuthError(400, "Role must be authority or admin")
        now = _now()
        row = {"email": email, "name": name, "role": role, "password_hash": hash_password(password),
               "active": True, "created_at": now, "password_changed_at": now, "last_login_at": None}
        if self.db:
            with self.store.engine.begin() as c:
                if c.execute(select(db.users.c.id).where(db.users.c.email == email)).first():
                    raise AuthError(409, "A user with that email already exists")
                row["id"] = c.execute(insert(db.users).values(**row).returning(db.users.c.id)).scalar_one()
        else:
            with self._lock:
                if any(u["email"] == email for u in self._users.values()):
                    raise AuthError(409, "A user with that email already exists")
                row["id"] = next(self._ids)
                self._users[row["id"]] = row
        return _public(row)

    def list_users(self) -> list[dict]:
        if self.db:
            with self.store.engine.connect() as c:
                rows = c.execute(select(db.users).order_by(db.users.c.id)).mappings().all()
            return [_public(dict(r)) for r in rows]
        return [_public(u) for u in sorted(self._users.values(), key=lambda u: u["id"])]

    def _get(self, user_id: int) -> dict | None:
        if self.db:
            with self.store.engine.connect() as c:
                r = c.execute(select(db.users).where(db.users.c.id == user_id)).mappings().first()
            return dict(r) if r else None
        return self._users.get(user_id)

    def _by_email(self, email: str) -> dict | None:
        if self.db:
            with self.store.engine.connect() as c:
                r = c.execute(select(db.users).where(db.users.c.email == email)).mappings().first()
            return dict(r) if r else None
        return next((u for u in self._users.values() if u["email"] == email), None)

    def update_user(self, user_id: int, *, name: str | None = None, role: str | None = None, active: bool | None = None,
                    password: str | None = None) -> dict:
        user = self._get(user_id)
        if not user:
            raise AuthError(404, "User not found")
        changes: dict = {}
        if name is not None:
            changes["name"] = name.strip()[:80] or user["name"]
        if role is not None:
            if role not in ROLES:
                raise AuthError(400, "Role must be authority or admin")
            changes["role"] = role
        if active is not None:
            changes["active"] = active
        if password is not None:
            changes["password_hash"] = hash_password(password)
            changes["password_changed_at"] = _now()
        # never leave the system without an active admin
        if (changes.get("role", user["role"]) != "admin" or changes.get("active", user["active"]) is False) \
                and user["role"] == "admin" and user["active"] and self._active_admins() <= 1:
            raise AuthError(409, "This is the last active admin")
        if changes:
            if self.db:
                with self.store.engine.begin() as c:
                    c.execute(update(db.users).where(db.users.c.id == user_id).values(**changes))
            else:
                with self._lock:
                    self._users[user_id].update(changes)
        # disabling a user or changing their password/role ends their sessions
        if active is False or password is not None or role is not None:
            self.end_user_sessions(user_id)
        return _public({**user, **changes})

    def _active_admins(self) -> int:
        if self.db:
            with self.store.engine.connect() as c:
                return c.execute(select(func.count()).select_from(db.users)
                                 .where(db.users.c.role == "admin", db.users.c.active.is_(True))).scalar_one()
        return sum(1 for u in self._users.values() if u["role"] == "admin" and u["active"])

    # ------------------------------------------------------------ login & sessions
    def authenticate(self, email: str, password: str) -> dict | None:
        try:
            email = normalize_email(email)
        except AuthError:
            verify_password(password, None)
            return None
        user = self._by_email(email)
        ok = verify_password(password, user["password_hash"] if user else None)
        if not ok or not user["active"]:
            return None
        return user

    def create_session(self, user: dict, ip: str, user_agent: str) -> str:
        token = new_token()
        now = _now()
        row = {"token_hash": token_hash(token), "user_id": user["id"], "created_at": now, "last_seen_at": now,
               "expires_at": now + SESSION_ABSOLUTE, "ip": ip[:64], "user_agent": (user_agent or "")[:200]}
        if self.db:
            with self.store.engine.begin() as c:
                c.execute(insert(db.sessions).values(**row))
                c.execute(update(db.users).where(db.users.c.id == user["id"]).values(last_login_at=now))
                c.execute(delete(db.sessions).where(db.sessions.c.expires_at < now))  # housekeeping
        else:
            with self._lock:
                self._sessions[row["token_hash"]] = row
                self._users[user["id"]]["last_login_at"] = now
        return token

    def session_user(self, token: str | None) -> dict | None:
        if not token or len(token) > 100:
            return None
        th, now = token_hash(token), _now()
        if self.db:
            with self.store.engine.begin() as c:
                r = c.execute(select(db.sessions, db.users.c.email, db.users.c.name, db.users.c.role, db.users.c.active)
                              .join(db.users, db.users.c.id == db.sessions.c.user_id)
                              .where(db.sessions.c.token_hash == th)).mappings().first()
                if not r:
                    return None
                if not r["active"] or r["expires_at"] < now or now - r["last_seen_at"] > SESSION_IDLE:
                    c.execute(delete(db.sessions).where(db.sessions.c.token_hash == th))
                    return None
                if now - r["last_seen_at"] > timedelta(minutes=1):  # sliding expiry, throttled
                    c.execute(update(db.sessions).where(db.sessions.c.token_hash == th).values(last_seen_at=now))
                return {"id": r["user_id"], "email": r["email"], "name": r["name"], "role": r["role"]}
        with self._lock:
            s = self._sessions.get(th)
            u = self._users.get(s["user_id"]) if s else None
            if not s or not u or not u["active"] or s["expires_at"] < now or now - s["last_seen_at"] > SESSION_IDLE:
                self._sessions.pop(th, None)
                return None
            s["last_seen_at"] = now
            return {"id": u["id"], "email": u["email"], "name": u["name"], "role": u["role"]}

    def end_session(self, token: str | None) -> None:
        if not token:
            return
        th = token_hash(token)
        if self.db:
            with self.store.engine.begin() as c:
                c.execute(delete(db.sessions).where(db.sessions.c.token_hash == th))
        else:
            with self._lock:
                self._sessions.pop(th, None)

    def end_user_sessions(self, user_id: int) -> None:
        if self.db:
            with self.store.engine.begin() as c:
                c.execute(delete(db.sessions).where(db.sessions.c.user_id == user_id))
        else:
            with self._lock:
                for k in [k for k, s in self._sessions.items() if s["user_id"] == user_id]:
                    del self._sessions[k]

    def change_password(self, user_id: int, current: str, new: str) -> None:
        user = self._get(user_id)
        if not user or not verify_password(current, user["password_hash"]):
            raise AuthError(400, "Current password is incorrect")
        self.update_user(user_id, password=new)

    # ------------------------------------------------------------ audit
    def audit(self, user: dict | None, action: str, detail: dict | None = None, ip: str | None = None) -> None:
        row = {"at": _now(), "user_id": user["id"] if user else None, "email": user["email"] if user else None,
               "action": action, "detail": db._snap(detail), "ip": ip}
        if self.db:
            self.store.put_global(insert(db.audit_log).values(**row))  # non-blocking
        else:
            with self._lock:
                self._audit.append(row)
                del self._audit[:-1000]

    def list_audit(self, limit: int = 100) -> list[dict]:
        if self.db:
            self.store.flush(2)
            with self.store.engine.connect() as c:
                rows = c.execute(select(db.audit_log).order_by(db.audit_log.c.id.desc()).limit(limit)).mappings().all()
            rows = [dict(r) for r in rows]
        else:
            rows = list(reversed(self._audit))[:limit]
        return [{**r, "at": r["at"].isoformat()} for r in rows]
