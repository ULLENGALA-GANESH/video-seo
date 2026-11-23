from datetime import UTC, datetime, timedelta
import logging

import bcrypt
import mongomock
import pytest
from fastapi.testclient import TestClient
from pymongo.errors import ServerSelectionTimeoutError

import account_v2
from app import ProtectOAuthCallback, app


def test_server_access_log_omits_google_callback_parameters():
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 0, '%s - "%s %s HTTP/%s" %d',
                               ("client", "GET", "/api/channel/oauth/callback?code=example&state=once", "1.1", 303), None)
    assert ProtectOAuthCallback().filter(record)
    assert '/api/channel/oauth/callback HTTP/1.1' in record.getMessage()
    assert "example" not in record.getMessage()


SIGNUP = {"name": "River", "email": "river@example.com", "password": "a private passphrase"}
ORIGIN = {"Origin": "http://testserver"}


@pytest.fixture
def workspace(monkeypatch):
    monkeypatch.setenv("INLINE_WORKER", "false")
    monkeypatch.delenv("APP_ORIGIN", raising=False)
    monkeypatch.delenv("COOKIE_SECURE", raising=False)
    db = mongomock.MongoClient(tz_aware=True).studio
    app.dependency_overrides[account_v2.get_db] = lambda: db
    try:
        with TestClient(app) as client:
            yield client, db
    finally:
        app.dependency_overrides.pop(account_v2.get_db, None)


def test_account_profile_and_session_revocation(workspace):
    client, db = workspace
    assert client.get("/api/auth/me").status_code == 401
    signed_up = client.post("/api/auth/signup", json=SIGNUP)
    assert signed_up.status_code == 201
    account = signed_up.json()
    assert account == {"id": str(db.users.find_one()["_id"]), "name": "River", "email": SIGNUP["email"]}
    assert "HttpOnly" in signed_up.headers["set-cookie"]
    assert "SameSite=lax" in signed_up.headers["set-cookie"]
    cookie = client.cookies.get(account_v2.COOKIE_NAME)
    assert db.web_sessions.find_one()["_id"] != cookie
    assert client.get("/api/auth/me").json() == account
    assert client.patch("/api/profile", json={"name": "  Riley  "}).json()["name"] == "Riley"
    assert client.get("/api/auth/me").json()["name"] == "Riley"
    signed_out = client.post("/api/auth/logout", headers=ORIGIN)
    assert signed_out.status_code == 204
    assert not signed_out.content
    assert client.get("/api/auth/me", headers={"Cookie": f"{account_v2.COOKIE_NAME}={cookie}"}).status_code == 401
    assert db.web_sessions.count_documents({}) == 0


def test_legacy_passwords_and_ids_survive_upgrade(workspace):
    client, db = workspace
    password = "older-pass"
    old_email = "Legacy@example.com"
    old_id = db.users.insert_one({
        "email": old_email, "username": "Legacy creator",
        "password": bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=4)).decode(),
    }).inserted_id
    duplicate = client.post("/api/auth/signup", json={**SIGNUP, "email": old_email.lower()})
    assert duplicate.status_code == 409
    account = client.post("/api/auth/login", json={"email": old_email.lower(), "password": password})
    assert account.status_code == 200
    assert account.json() == {"id": str(old_id), "name": "Legacy creator", "email": old_email}
    stored = db.users.find_one({"_id": old_id})
    assert "password" not in stored
    assert stored["password_hash"].startswith("$argon2id$")
    assert client.post("/api/auth/login", json={"email": old_email, "password": password}).status_code == 200
    assert db.web_sessions.count_documents({"user_id": old_id}) == 1


def test_bad_credentials_validation_and_browser_origin(workspace):
    client, _ = workspace
    bad = client.post("/api/auth/signup", json={**SIGNUP, "password": "short"})
    assert bad.status_code == 422
    assert "short" not in bad.text
    assert client.post("/api/auth/signup", json=SIGNUP, headers={"Origin": "https://other.example"}).status_code == 403
    assert client.post("/api/auth/signup", json=SIGNUP, headers=ORIGIN).status_code == 201
    assert client.post("/api/auth/signup", json={**SIGNUP, "email": "RIVER@example.com"}).status_code == 409
    invalid = client.post("/api/auth/login", json={"email": SIGNUP["email"], "password": "incorrect"})
    unknown = client.post("/api/auth/login", json={"email": "nobody@example.com", "password": "incorrect"})
    assert invalid.status_code == unknown.status_code == 401
    assert invalid.json() == unknown.json()
    assert client.post("/api/auth/logout", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.get("/api/auth/me").status_code == 200


def test_expired_sessions_and_secure_origin(workspace, monkeypatch):
    client, db = workspace
    created = client.post("/api/auth/signup", json=SIGNUP)
    assert created.status_code == 201
    db.web_sessions.update_many({}, {"$set": {"expires_at": datetime.now(UTC) - timedelta(minutes=1)}})
    assert client.get("/api/auth/me").status_code == 401
    monkeypatch.setenv("APP_ORIGIN", "https://studio.example")
    credentials = {"email": SIGNUP["email"], "password": SIGNUP["password"]}
    assert client.post("/api/auth/login", json=credentials, headers=ORIGIN).status_code == 403
    login = client.post("/api/auth/login", json=credentials, headers={"Origin": "https://studio.example"})
    assert login.status_code == 200
    assert "Secure" in login.headers["set-cookie"]


def test_spa_assets_and_database_independent_health(workspace, monkeypatch, tmp_path):
    client, _ = workspace
    (tmp_path / "index.html").write_text("<html>Studio shell</html>", encoding="utf-8")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets/main.js").write_text("export {};", encoding="utf-8")
    monkeypatch.setenv("FRONTEND_DIST", str(tmp_path))
    for path in ["/", "/demo", "/analyses/a123"]:
        assert "Studio shell" in client.get(path).text
    assert client.get("/assets/main.js").text == "export {};"
    assert client.get("/assets/missing.js").status_code == 404
    assert client.get("/api").status_code == 404
    assert client.get("/api/missing").headers["content-type"].startswith("application/json")
    assert client.get("/api/auth/login").status_code == 405
    assert client.get("/api/ready").json() == {"status": "ready"}

    def offline_database():
        raise ServerSelectionTimeoutError("private internal detail")

    app.dependency_overrides[account_v2.get_db] = offline_database
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/demo").status_code == 200
    ready = client.get("/api/ready")
    assert ready.status_code == 503
    assert "internal detail" not in ready.text
