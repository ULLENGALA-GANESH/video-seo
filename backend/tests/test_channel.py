import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import mongomock
import pytest
from bson import ObjectId
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import channel


@pytest.fixture
def setup(monkeypatch):
    db = mongomock.MongoClient(tz_aware=True).studio
    owner = {"_id": ObjectId()}
    for key, value in {
        "YOUTUBE_CLIENT_ID": "test-id",
        "YOUTUBE_CLIENT_SECRET": "test-secret",
        "YOUTUBE_REDIRECT_URI": "https://studio.example/api/channel/oauth/callback",
        "YOUTUBE_TOKEN_KEY": Fernet.generate_key().decode(),
    }.items():
        monkeypatch.setenv(key, value)
    app = FastAPI()
    app.include_router(channel.router)
    app.dependency_overrides[channel.get_db] = lambda: db
    app.dependency_overrides[channel.current_user] = lambda: owner
    monkeypatch.setattr(
        channel, "transport", lambda *a, **kw: pytest.fail("Unexpected outbound HTTP call.")
    )
    with TestClient(app, follow_redirects=False) as client:
        client.cookies.set(channel.COOKIE_NAME, "opaque-test-session")
        yield client, db, owner


def link(db, user):
    cipher = channel.config()[3]
    db.youtube_channels.insert_one(
        {
            "_id": user["_id"],
            "channel": {
                "id": "mine",
                "title": "My channel",
                "uploads": "playlist",
                "views": 995,
                "subscribers": 20,
            },
            "generation": "one",
            "access": cipher.encrypt(b"access").decode(),
            "refresh": cipher.encrypt(b"refresh").decode(),
            "expires": (datetime.now(UTC) + timedelta(hours=1)).timestamp(),
        }
    )


def fetch_channel():
    return {
        "items": [
            {
                "id": "mine",
                "snippet": {"title": "My channel"},
                "contentDetails": {"relatedPlaylists": {"uploads": "playlist"}},
                "statistics": {"viewCount": "1010", "subscriberCount": "22", "videoCount": "3"},
            }
        ]
    }


def test_oauth_binds_state_to_session_consumes_once_and_encrypts_credentials(setup, monkeypatch):
    client, db, _ = setup

    def provider(method, url, *, headers=None, body=None):
        if url == channel.TOKEN:
            form = parse_qs(body.decode())
            assert form["code_verifier"] and form["code"] == ["callback-code"]
            return (
                200,
                {},
                {
                    "access_token": "private-access",
                    "refresh_token": "private-refresh",
                    "expires_in": 3600,
                    "scope": " ".join(channel.SCOPES),
                },
            )
        assert (
            url.startswith(channel.DATA + "channels?")
            and headers["Authorization"] == "Bearer private-access"
        )
        return 200, {}, fetch_channel()

    monkeypatch.setattr(channel, "transport", provider)
    params = parse_qs(urlsplit(client.post("/api/channel/oauth/start").json()["url"]).query)
    assert params["code_challenge_method"] == ["S256"] and params["scope"][0].split() == list(
        channel.SCOPES
    )
    args = {"state": params["state"][0], "code": "callback-code"}
    client.cookies.set(channel.COOKIE_NAME, "different")
    assert "expired" in client.get("/api/channel/oauth/callback", params=args).headers["location"]
    client.cookies.set(channel.COOKIE_NAME, "opaque-test-session")
    assert "success" in client.get("/api/channel/oauth/callback", params=args).headers["location"]
    record = db.youtube_channels.find_one()
    assert "private-access" not in str(record) and "private-refresh" not in str(record)
    assert channel.decrypt(channel.config()[3], record["refresh"]) == "private-refresh"
    assert "expired" in client.get("/api/channel/oauth/callback", params=args).headers["location"]
    assert "refresh" not in client.get("/api/channel").json()


def test_unconfigured_status_and_local_erasure_even_when_google_is_unavailable(setup, monkeypatch):
    client, db, user = setup
    link(db, user)
    for event_id in ("youtube:upload:v", "analysis:local"):
        db.activity.insert_one({"event_id": event_id, "user_id": user["_id"]})
    db.youtube_uploads.insert_one({"owner": user["_id"]})
    monkeypatch.delenv("YOUTUBE_TOKEN_KEY")
    assert not client.get("/api/channel").json()["configured"]
    assert client.delete("/api/channel").json()["revocation"] == "unconfirmed"
    assert db.youtube_channels.count_documents({}) == db.youtube_uploads.count_documents({}) == 0
    assert db.activity.find_one()["event_id"] == "analysis:local"


def test_analytics_reads_real_rows_and_notifies_on_first_observed_crossing(setup, monkeypatch):
    client, db, user = setup
    link(db, user)

    def provider(method, url, **kwargs):
        if "/channels?" in url:
            return 200, {}, fetch_channel()
        assert "channel%3D%3DMINE" in url
        return (
            200,
            {},
            {"columnHeaders": [{"name": "day"}, {"name": "views"}], "rows": [["2026-11-03", 42]]},
        )

    monkeypatch.setattr(channel, "transport", provider)
    assert client.get("/api/channel/analytics").json()["rows"] == [{"day": "2026-11-03", "views": 42}]
    assert client.get("/api/channel/analytics").status_code == 200
    assert db.activity.count_documents({}) == 1


def test_update_is_owner_scoped_and_preserves_category_and_visibility(setup, monkeypatch):
    client, db, user = setup
    link(db, user)
    video = {
        "id": "vid",
        "etag": "etag",
        "snippet": {
            "title": "Before",
            "description": "Original",
            "categoryId": "24",
            "defaultLanguage": "en",
            "channelId": "another",
        },
        "status": {"privacyStatus": "private"},
    }
    calls = []

    def provider(method, url, *, headers=None, body=None):
        calls.append(method)
        if method == "GET":
            return 200, {}, {"items": [video]}
        patch = json.loads(body)
        assert patch["snippet"]["categoryId"] == "24" and patch["snippet"]["defaultLanguage"] == "en"
        assert "status" not in patch and headers["If-Match"] == "etag"
        return 200, {}, {**video, "etag": "changed", "snippet": patch["snippet"]}

    monkeypatch.setattr(channel, "transport", provider)
    data = {
        "title": "After",
        "description": "Updated",
        "tags": ["native plants"],
        "etag": "etag",
        "confirmed": True,
    }
    assert client.patch("/api/channel/videos/vid", json=data).status_code == 404
    video["snippet"]["channelId"] = "mine"
    assert client.patch("/api/channel/videos/vid", json={**data, "confirmed": False}).status_code == 422
    assert client.patch("/api/channel/videos/vid", json={**data, "etag": "stale"}).status_code == 409
    assert client.patch("/api/channel/videos/vid", json=data).json()["visibility"] == "private"
    assert calls.count("PUT") == 1 and db.activity.count_documents({}) == 1


def test_resumable_transfer_recovers_ambiguous_ack_and_never_publishes_twice(setup, monkeypatch):
    client, db, user = setup
    link(db, user)
    total = 256 * 1024 + 3
    start_data = {
        "request_id": str(uuid4()),
        "title": "Test",
        "description": "Description",
        "tags": [],
        "mime": "video/mp4",
        "size": total,
        "made_for_kids": False,
        "confirmed": True,
    }
    location = "https://www.googleapis.com/upload/session?upload_id=private-session"
    ranges = []

    def provider(method, url, *, headers=None, body=None):
        if method == "POST":
            assert (
                json.loads(body)["status"]["privacyStatus"] == "private"
                and "notifySubscribers=false" in url
            )
            return 200, {"location": location}, {}
        assert url == location
        bounds = headers["Content-Range"]
        ranges.append(bounds)
        if len(ranges) == 1:
            raise HTTPException(503, "Provider did not acknowledge the request.")
        if bounds.startswith("bytes */"):
            return 308, {"range": "bytes=0-262143"}, {}
        return 201, {}, {"id": "video123", "status": {"privacyStatus": "private"}}

    monkeypatch.setattr(channel, "transport", provider)
    assert (
        client.post("/api/channel/uploads", json={**start_data, "confirmed": False}).status_code == 422
    )
    start = client.post("/api/channel/uploads", json=start_data).json()
    assert start == client.post("/api/channel/uploads", json=start_data).json()
    path = "/api/channel/uploads/" + start["id"]
    assert "private-session" not in str(db.youtube_uploads.find_one())
    first_range = {"Content-Range": f"bytes 0-262143/{total}"}
    assert client.put(path, content=bytes(256 * 1024), headers=first_range).status_code == 503
    assert (
        client.put(path, content=bytes(256 * 1024), headers=first_range).json()["offset"] == 256 * 1024
    )
    opts = {"content": b"end", "headers": {"Content-Range": f"bytes 262144-262146/{total}"}}
    assert client.put(path, **opts).json()["video_id"] == "video123"
    assert client.put(path, **opts).json()["video_id"] == "video123"
    assert len(ranges) == 3 and db.activity.count_documents({}) == 1
    assert "session" not in db.youtube_uploads.find_one()
    user["_id"] = ObjectId()
    link(db, user)
    assert client.get(path).status_code == 404


def test_quota_is_explicit_without_provider_error_leak():
    with pytest.raises(HTTPException) as failure:
        channel.checked(
            (
                403,
                {},
                {
                    "error": {
                        "errors": [{"reason": "quotaExceeded"}],
                        "message": "private provider content",
                    }
                },
            )
        )
    assert failure.value.status_code == 429 and "private" not in failure.value.detail


def test_stale_report_scope_and_user_confirmation_prevent_upload_initialization(setup):
    client, db, user = setup
    link(db, user)
    report = db.analyses_v2.insert_one(
        {
            "user_id": user["_id"],
            "transcript_revision": 3,
            "metadata_revision": 4,
            "metadata_draft": {"source_revision": 3},
        }
    ).inserted_id
    data = {
        "title": "Reviewed title",
        "description": "",
        "tags": [],
        "confirmed": True,
        "request_id": str(uuid4()),
        "size": 32,
        "mime": "video/mp4",
        "made_for_kids": False,
        "report_id": str(report),
        "metadata_revision": 2,
        "transcript_revision": 3,
    }
    assert client.post("/api/channel/uploads", json=data).status_code == 409
    assert client.post("/api/channel/uploads", json={**data, "confirmed": False}).status_code == 422
    assert (
        client.post("/api/channel/uploads", json={**data, "report_id": str(ObjectId())}).status_code
        == 404
    )
    assert db.youtube_uploads.count_documents({}) == 0


def test_partial_google_consent_rejected_and_source_report_survives_redirect(setup, monkeypatch):
    client, db, _ = setup
    report = str(ObjectId())
    auth = client.post("/api/channel/oauth/start", params={"report": report}).json()["url"]
    state = parse_qs(urlsplit(auth).query)["state"][0]
    monkeypatch.setattr(
        channel,
        "transport",
        lambda *a, **kw: (
            200,
            {},
            {
                "access_token": "access",
                "refresh_token": "refresh",
                "scope": channel.SCOPES[0],
                "expires_in": 3600,
            },
        ),
    )
    response = client.get(
        "/api/channel/oauth/callback", params={"state": state, "code": "authorization"}
    )
    assert parse_qs(urlsplit(response.headers["location"]).query) == {
        "connection": ["permissions"],
        "report": [report],
    }
    assert db.youtube_channels.count_documents({}) == 0
