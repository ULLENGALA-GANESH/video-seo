from datetime import timedelta
from io import BytesIO
from urllib.error import HTTPError
import pytest
import mongomock
from bson import ObjectId
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import research


@pytest.fixture
def workspace(monkeypatch):
    db = mongomock.MongoClient(tz_aware=True).research_tests
    user = {"_id": ObjectId()}
    report = ObjectId()
    db.analyses_v2.insert_one({
        "_id": report, "user_id": user["_id"], "status": "completed",
        "keywords": [{"phrase": "blue hour"}],
    })
    app = FastAPI()
    app.include_router(research.router)
    app.dependency_overrides[research.get_db] = lambda: db
    app.dependency_overrides[research.current_user] = lambda: user
    monkeypatch.setenv("YOUTUBE_API_KEY", "fixture-only")
    monkeypatch.setenv("YOUTUBE_RESEARCH_DAILY_LIMIT", "60")
    monkeypatch.setattr(research, "sample_keywords", lambda: [{"phrase": "alpine photography"}])
    calls = []

    def provider(phrase, region, key):
        assert key == "fixture-only"
        calls.append((phrase, region))
        return {"phrase": phrase, "region": region, "collected_at": "2026-01-01T00:00:00Z", "videos": []}

    monkeypatch.setattr(research, "fetch_youtube", provider)
    with TestClient(app) as client:
        yield client, db, user, {"report_id": str(report), "phrase": "blue hour", "region": "US"}, calls


def test_real_provider_contract_preserves_source_order_and_missing_counters(monkeypatch):
    calls = []
    one, two, absent = "a" * 11, "b" * 11, "c" * 11
    def fake(endpoint, params, _):
        calls.append((endpoint, params))
        if endpoint == "search":
            return [{"id": {"videoId": val}} for val in (one, two, absent, one)]
        return [
            {"id": two, "snippet": {"title": "Second", "channelTitle": "Channel"}, "statistics": {"viewCount": "42"}},
            {"id": one, "snippet": {"title": "First"}, "statistics": {"viewCount": "0", "likeCount": "2", "commentCount": "3"}},
        ]
    monkeypatch.setattr(research, "google", fake)
    result = research.fetch_youtube("blue hour", "IN", "fixture")
    assert calls[0][1]["regionCode"] == "IN" and calls[0][1]["order"] == "relevance"
    assert calls[1][1]["id"] == ",".join((one, two, absent))
    assert [row["video_id"] for row in result["videos"]] == [one, two]
    assert result["videos"][0]["views"] == 0 and result["videos"][1]["likes"] is None
    assert result["videos"][0]["url"] == "https://www.youtube.com/watch?v=" + one
    assert "competition" not in result and "search_volume" not in result


def test_ownership_case_insensitive_selection_region_cache_and_expiry(workspace):
    client, db, user, query, calls = workspace
    assert client.post("/api/research", json={**query, "phrase": "arbitrary"}).status_code == 422
    assert client.post("/api/research", json={**query, "region": "ZZ"}).status_code == 422
    first = client.post("/api/research", json=query)
    assert first.status_code == 200, first.text
    assert first.json()["cached"] is False
    assert client.post("/api/research", json={**query, "phrase": "BLUE HOUR"}).json()["cached"] is True
    assert client.post("/api/research", json={**query, "region": "IN"}).json()["cached"] is False
    expired = research.datetime.now(research.UTC) - timedelta(minutes=1)
    db.research_cache.update_many({}, {"$set": {"expires_at": expired}})
    assert client.post("/api/research", json=query).json()["cached"] is False
    user["_id"] = ObjectId()
    assert client.post("/api/research", json=query).status_code == 404
    assert len(calls) == 3


def test_sample_allowlist_rate_and_project_budget(workspace, monkeypatch):
    client, _, _, _, calls = workspace
    path = "/api/research/sample"
    approved = {"report_id": "sample", "phrase": "alpine photography", "region": "GB"}
    assert client.post(path, json={**approved, "phrase": "other"}).status_code == 422
    assert client.post(path, json={**approved, "report_id": str(ObjectId())}).status_code == 404
    for region in ["GB", "US", "IN"]:
        assert client.post(path, json={**approved, "region": region}).status_code == 200
    limited = client.post(path, json={**approved, "region": "DE"})
    assert limited.status_code == 429 and int(limited.headers["Retry-After"]) > 0
    assert client.post(path, json=approved).json()["cached"] and len(calls) == 3


def test_unavailable_budget_and_lease(workspace, monkeypatch):
    client, db, user, query, calls = workspace
    monkeypatch.delenv("YOUTUBE_API_KEY")
    assert client.post("/api/research", json=query).status_code == 503
    assert not calls
    monkeypatch.setenv("YOUTUBE_API_KEY", "fixture-only")
    actor = "user:" + str(user["_id"])
    db.research_cache.insert_one({
        "_id": research.digest(actor, "blue hour", "US"), "lease": "existing",
        "expires_at": research.datetime.now(research.UTC) + timedelta(minutes=1),
    })
    assert client.post("/api/research", json=query).status_code == 409
    monkeypatch.setenv("YOUTUBE_RESEARCH_DAILY_LIMIT", "1")
    assert client.post("/api/research", json={**query, "region": "IN"}).status_code == 200
    assert client.post("/api/research", json={**query, "region": "CA"}).status_code == 429
    assert len(calls) == 1


def test_editorial_assessment_round_trip_scoping_and_clear(workspace, monkeypatch):
    client, _, user, query, calls = workspace
    monkeypatch.delenv("YOUTUBE_API_KEY")
    read, write = "/api/research/assessment/read", "/api/research/assessment"
    assert client.post(read, json=query).json()["level"] == "unrated"
    saved = client.put(write, json={**query, "level": "moderate", "notes": "Editorial judgment"}).json()
    assert saved["level"] == "moderate" and saved["updated_at"]
    assert client.post(read, json=query).json() == saved
    assert client.post(read, json={**query, "region": "IN"}).json()["level"] == "unrated"
    original = user["_id"]
    user["_id"] = ObjectId()
    assert client.post(read, json=query).status_code == 404
    assert client.put(write, json={**query, "level": "low", "notes": ""}).status_code == 404
    user["_id"] = original
    assert client.put(write, json={**query, "level": "unrated", "notes": ""}).json()["updated_at"] is None
    assert client.post(read, json=query).json()["level"] == "unrated"
    assert calls == []


def test_provider_failure_leaves_no_successful_cache_entry(workspace, monkeypatch):
    client, db, _, query, _ = workspace
    def unavailable(*args):
        raise HTTPException(502, "YouTube unavailable")
    monkeypatch.setattr(research, "fetch_youtube", unavailable)
    assert client.post("/api/research", json=query).status_code == 502
    assert db.research_cache.count_documents({}) == 0


def test_provider_quota_failure_is_sanitized(monkeypatch):
    def quota_error(request, timeout):
        assert "fixture-key" not in request.full_url
        assert request.get_header("X-goog-api-key") == "fixture-key"
        body = b'{"error":{"errors":[{"reason":"quotaExceeded"}],"message":"sensitive detail"}}'
        raise HTTPError(request.full_url, 403, "Denied", {}, BytesIO(body))

    monkeypatch.setattr(research, "urlopen", quota_error)
    with pytest.raises(HTTPException) as raised:
        research.google("search", {"q": "fixture-only"}, "fixture-key")
    assert raised.value.status_code == 429
    assert "sensitive detail" not in raised.value.detail
