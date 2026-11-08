"""Authentication, authorisation and HTTP hardening."""
import base64
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import auth as auth_mod
from app.engine import Engine
from app.main import create_app
from conftest import TEST_PASSWORD, sign_in


@pytest.fixture()
def client():
    with TestClient(create_app(Engine())) as c:
        yield c


def test_protected_routes_need_sign_in(client):
    for method, path in [("post", "/api/scenario"), ("post", "/api/weather"), ("post", "/api/settings"),
                         ("post", "/api/incidents/NKS-0001/execute"), ("get", "/api/auth/me"), ("get", "/api/users"),
                         ("get", "/api/audit"), ("get", "/api/history/runs"), ("get", "/api/reports/R-0001/photo")]:
        r = getattr(client, method)(path, json={}) if method == "post" else client.get(path)
        assert r.status_code == 401, (path, r.status_code)
    # public routes stay open
    assert client.get("/api/state").status_code == 200
    assert client.get("/api/health").json()["status"] == "ok"


def test_login_sets_hardened_cookie_and_logout_ends_session(client):
    me = sign_in(client, "authority")
    assert me["role"] == "authority" and "password_hash" not in me
    cookie = client.cookies.get("nikaasi_session")
    assert cookie and len(cookie) >= 40
    login = client.post("/api/auth/login", json={"email": "authority@test.local", "password": TEST_PASSWORD})
    sc = login.headers["set-cookie"].lower()
    assert "httponly" in sc and "samesite=strict" in sc and "path=/" in sc
    cookie = client.cookies.get("nikaasi_session")  # the session we are about to end
    assert client.get("/api/auth/me").status_code == 200
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401
    # the old token is dead even if replayed
    client.cookies.set("nikaasi_session", cookie)
    assert client.get("/api/auth/me").status_code == 401


def test_wrong_password_and_unknown_user_look_the_same(client):
    sign_in(client, "authority")
    client.post("/api/auth/logout")
    a = client.post("/api/auth/login", json={"email": "authority@test.local", "password": "nope-nope-1"})
    b = client.post("/api/auth/login", json={"email": "ghost@test.local", "password": "nope-nope-1"})
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json() == {"error": "Wrong email or password"}


def test_account_lockout_after_repeated_failures(client):
    sign_in(client, "authority")
    client.post("/api/auth/logout")
    for _ in range(5):
        assert client.post("/api/auth/login", json={"email": "authority@test.local", "password": "bad-guess-1"}).status_code == 401
    r = client.post("/api/auth/login", json={"email": "authority@test.local", "password": TEST_PASSWORD})
    assert r.status_code == 429, "even the right password is refused while locked"


def test_roles_admin_only_routes(client):
    sign_in(client, "authority")
    assert client.get("/api/users").status_code == 403
    assert client.post("/api/scenario", json={"action": "step"}).status_code == 200
    client.post("/api/auth/logout")
    sign_in(client, "admin")
    assert client.get("/api/users").status_code == 200


def test_user_management_and_session_revocation(client):
    sign_in(client, "admin")
    weak = client.post("/api/users", json={"email": "op@test.local", "name": "Op", "role": "authority", "password": "short"})
    assert weak.status_code == 400
    r = client.post("/api/users", json={"email": "Op@Test.local", "name": "Op", "role": "authority", "password": TEST_PASSWORD})
    assert r.status_code == 201 and r.json()["email"] == "op@test.local"
    assert client.post("/api/users", json={"email": "op@test.local", "password": TEST_PASSWORD}).status_code == 409
    uid = r.json()["id"]

    with TestClient(client.app) as op:  # a second browser signed in as the operator
        assert op.post("/api/auth/login", json={"email": "op@test.local", "password": TEST_PASSWORD}).status_code == 200
        assert op.get("/api/auth/me").status_code == 200
        assert client.patch(f"/api/users/{uid}", json={"active": False}).status_code == 200
        assert op.get("/api/auth/me").status_code == 401, "disabling a user ends their sessions"
        assert op.post("/api/auth/login", json={"email": "op@test.local", "password": TEST_PASSWORD}).status_code == 401

    admin_id = client.get("/api/auth/me").json()["id"]
    r = client.patch(f"/api/users/{admin_id}", json={"role": "authority"})
    assert r.status_code == 409, "cannot demote the last active admin"
    actions = [a["action"] for a in client.get("/api/audit").json()]
    assert {"user.created", "user.updated", "login.ok"} <= set(actions)
    assert "password" not in str(client.get("/api/audit").json()).replace('"password": "reset"', "").replace("password.changed", "")


def test_password_change_requires_current_password(client):
    sign_in(client, "authority")
    assert client.post("/api/auth/password", json={"current": "wrong-pass-1", "new": "Another pass 77"}).status_code == 400
    assert client.post("/api/auth/password", json={"current": TEST_PASSWORD, "new": "Another pass 77"}).status_code == 200
    assert client.get("/api/auth/me").status_code == 200, "this browser stays signed in"
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"email": "authority@test.local", "password": "Another pass 77"}).status_code == 200


def test_session_idle_expiry(client, monkeypatch):
    sign_in(client, "authority")
    monkeypatch.setattr(auth_mod, "SESSION_IDLE", timedelta(seconds=-1))
    assert client.get("/api/auth/me").status_code == 401


def test_csrf_cross_site_post_is_blocked(client):
    sign_in(client, "authority")
    evil = client.post("/api/scenario", json={"action": "step"}, headers={"origin": "https://evil.example"})
    assert evil.status_code == 403
    same = client.post("/api/scenario", json={"action": "step"}, headers={"origin": "http://testserver"})
    assert same.status_code == 200


def test_security_headers(client):
    h = client.get("/").headers
    assert "default-src 'self'" in h["content-security-policy"] and "frame-ancestors 'none'" in h["content-security-policy"]
    assert h["x-frame-options"] == "DENY" and h["x-content-type-options"] == "nosniff"
    assert client.get("/api/health").headers["cache-control"] == "no-store"


def test_report_rate_limit(client):
    codes = [client.post("/api/reports", json={"sector": "G-9", "text": "Pani bhar gaya hai", "reporterId": f"u{i}"}).status_code
             for i in range(12)]
    assert codes[:10] == [200] * 10 and codes[10] == 429


def test_photo_validation(client):
    fake = "data:image/jpeg;base64," + base64.b64encode(b"<svg onload=alert(1)>").decode()
    assert client.post("/api/reports", json={"sector": "G-9", "text": "pani hai", "photo": fake}).status_code == 400
    svg = "data:image/svg+xml;base64," + base64.b64encode(b"<svg/>").decode()
    assert client.post("/api/reports", json={"sector": "G-9", "text": "pani hai", "photo": svg}).status_code == 400
    jpg = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff\xe0" + b"0" * 64).decode()
    r = client.post("/api/reports", json={"sector": "G-9", "text": "pani hai", "photo": jpg})
    assert r.status_code == 200 and r.json()["report"]["hasPhoto"] is True
    rid = r.json()["report"]["id"]
    assert client.get(f"/api/reports/{rid}/photo").status_code == 401, "citizen photos are private"
    sign_in(client, "authority")
    assert client.get(f"/api/reports/{rid}/photo").headers["content-type"] == "image/jpeg"


def test_oversized_body_rejected(client):
    r = client.post("/api/reports", content=b"x" * 3_100_000, headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_production_mode_hides_docs_and_uses_secure_cookie(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    with TestClient(create_app(Engine()), base_url="https://testserver") as c:
        assert c.get("/docs").status_code == 404
        assert c.get("/openapi.json").status_code == 404
        c.app.state.auth.create_user("a@test.local", "A", "admin", TEST_PASSWORD)
        r = c.post("/api/auth/login", json={"email": "a@test.local", "password": TEST_PASSWORD})
        sc = r.headers["set-cookie"]
        assert sc.startswith("__Host-nikaasi_session=") and "Secure" in sc
        assert "strict-transport-security" in c.get("/").headers


def test_staff_pages_are_not_served_to_signed_out_visitors(client):
    home = client.get("/").text
    assert "authority.html" not in home and "admin.html" not in home and "login" not in home.lower(), \
        "the public home page must not link to the staff area"
    for path in ("/authority.html", "/authority", "/admin.html", "/admin"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/login.html?next=/"), path
        assert "Command" not in r.text
    assert client.get("/login.html").headers["x-robots-tag"] == "noindex, nofollow"


def test_staff_pages_by_role(client):
    sign_in(client, "authority")
    r = client.get("/authority.html", follow_redirects=False)
    assert r.status_code == 200 and "Nikaasi Command" in r.text and r.headers["x-robots-tag"].startswith("noindex")
    r = client.get("/admin.html", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/authority.html", "authority users can't open admin"
    client.post("/api/auth/logout")
    sign_in(client, "admin")
    assert client.get("/admin.html", follow_redirects=False).status_code == 200
