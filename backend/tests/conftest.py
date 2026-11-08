import os

# Keep tests offline and deterministic even when nikaasi/.env holds real values:
# an empty value also stops the .env loader from filling these in.
os.environ["GEMINI_API_KEY"] = ""
os.environ["DATABASE_URL"] = ""
os.environ["ADMIN_EMAIL"] = ""
os.environ["ADMIN_PASSWORD"] = ""
os.environ["APP_ENV"] = "development"
os.environ.pop("COOKIE_SECURE", None)

TEST_PASSWORD = "Test-pass 4 Nikaasi"


def sign_in(client, role="admin", email=None):
    """Create a user directly in the app's auth service and log the test client in."""
    email = email or f"{role}@test.local"
    auth = client.app.state.auth
    if not any(u["email"] == email for u in auth.list_users()):
        auth.create_user(email, role.title(), role, TEST_PASSWORD)
    r = client.post("/api/auth/login", json={"email": email, "password": TEST_PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()

os.environ["WEATHER_SOURCE"] = "demo"  # tests never call the live weather API
