from copy import deepcopy
import json
from pathlib import Path

import mongomock
import pytest
from bson import ObjectId
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import editor
from analysis_text import summarize
from analysis_v2 import build_job


@pytest.fixture
def studio():
    db = mongomock.MongoClient().studio
    owner = {"_id": ObjectId()}
    app = FastAPI()
    app.include_router(editor.router)
    app.dependency_overrides[editor.get_db] = lambda: db
    app.dependency_overrides[editor.current_user] = lambda: owner
    job = build_job(owner["_id"], "Garden", "video")
    parts = [
        {"text": "Basil roots grow.", "start_seconds": 0.0, "end_seconds": 4.2},
        {"text": "Mint roots spread.", "start_seconds": 7.0, "end_seconds": 11.3},
    ]
    job.update(summarize(parts, job["title"]), status="completed", duration_seconds=11.3,
               speech_coverage={"partial": True, "unrecognized_ranges": [{"start_seconds": 4.2, "end_seconds": 7.0}]})
    db.analyses_v2.insert_one(job)
    with TestClient(app) as client:
        yield client, db.analyses_v2, owner, job, "/api/analyses/" + str(job["_id"])


def suggestion(text, index=0):
    return {"text": text, "reason": "Supported by the cited passage.", "segment_index": index}


def draft():
    return editor.Draft.model_validate({
        "titles": [suggestion("Growing basil")], "descriptions": [suggestion("Discover growing herbs.")],
        "tags": [suggestion("basil")], "chapters": [suggestion("Basil"), suggestion("Mint", 1)],
    })


def test_bundled_model_draft_cites_the_public_sample():
    public = Path(__file__).resolve().parents[2] / "frontend" / "public"
    sample = json.loads((public / "sample-report.json").read_text(encoding="utf-8"))
    metadata = json.loads((public / "sample-metadata.json").read_text(encoding="utf-8"))
    assert metadata.pop("model")
    checked = editor.Draft.model_validate(metadata)
    editor.validate_items(checked, editor.excerpts(sample["segments"]), generated=True)
    assert checked.chapters
    assert all(sample["segments"][chapter.segment_index]["start_seconds"] is not None for chapter in checked.chapters)


def test_timed_correction_preserves_ranges_coverage_and_owner(studio):
    client, jobs, owner, job, path = studio
    data = {"segments": ["Lavender flowers bloom.", "Mint roots spread."], "transcript_revision": 0}
    assert client.put(path + "/transcript", json={**data, "segments": ["One changed interval"]}).status_code == 422
    assert client.put(path + "/transcript", json={"transcript_revision": 0, "transcript": "Changed"}).status_code == 422
    revised = client.put(path + "/transcript", json=data)
    assert revised.status_code == 200
    report = revised.json()
    assert report["transcript_revision"] == 1 and report["segments"][0]["text"] == data["segments"][0]
    assert report["segments"][1] == job["segments"][1]
    assert report["segments"][0]["end_seconds"] == job["segments"][0]["end_seconds"]
    assert report["speech_coverage"] == job["speech_coverage"]
    assert jobs.find_one()["_id"] == job["_id"] and jobs.find_one()["user_id"] == owner["_id"]
    assert any("lavender" in term["phrase"] for term in report["keywords"])
    assert client.put(path + "/transcript", json=data).status_code == 409
    assert client.put(path + "/transcript", json={**data, "transcript_revision": 1}).json()["transcript_revision"] == 1
    owner["_id"] = ObjectId()
    for response in (client.put(path + "/transcript", json=data), client.get(path + "/metadata")):
        assert response.status_code == 404


def test_untimed_legacy_correction_accepts_text_and_preserves_paragraphs(studio):
    client, jobs, _, _, path = studio
    jobs.update_one({}, {"$set": {"source_type": "transcript", "transcript": "Old text."}, "$unset": {"segments": ""}})
    text = "Build a raised garden.\nWater the raised garden."
    result = client.put(path + "/transcript", json={"transcript_revision": 0, "transcript": text})
    assert result.status_code == 200
    assert result.json()["transcript"] == text == jobs.find_one()["source_text"]
    assert all(part["start_seconds"] is None for part in result.json()["segments"])


def test_draft_edit_stale_evidence_and_optimistic_lock(studio, monkeypatch):
    client, _, _, _, path = studio
    monkeypatch.setattr(editor, "infer", lambda job: (draft(), "local-test"))
    assert client.get(path + "/metadata").json()["draft"] is None
    versions = {"revision": 0, "transcript_revision": 0}
    response = client.post(path + "/metadata/generate", json=versions)
    assert response.status_code == 200
    first = response.json()
    assert first["revision"] == 1 and not first["stale"]
    assert first["draft"]["evidence"]["0"] == {"text": "Basil roots grow.", "start_seconds": 0.0}
    edited = draft().model_dump()
    edited["titles"][0]["text"] = "My gardening notes"
    data = {"revision": 1, "transcript_revision": 0, "draft": edited}
    bad = deepcopy(data)
    bad["draft"]["titles"][0]["segment_index"] = 30
    assert client.put(path + "/metadata", json=bad).status_code == 422
    assert client.put(path + "/metadata", json=data).json()["revision"] == 2
    assert client.put(path + "/metadata", json=data).status_code == 409
    assert client.put(path + "/transcript", json={"transcript_revision": 0, "segments": ["Lemon balm.", "Mint roots spread."]}).status_code == 200
    stale = client.get(path + "/metadata").json()
    assert stale["stale"] and stale["draft"]["evidence"]["0"]["text"] == "Basil roots grow."
    assert client.put(path + "/metadata", json={**data, "revision": 2, "transcript_revision": 1}).status_code == 409


def test_model_result_cannot_overwrite_a_concurrent_correction(studio, monkeypatch):
    client, jobs, _, _, path = studio
    def generate_while_editing(job):
        jobs.update_one({"_id": job["_id"]}, {"$inc": {"transcript_revision": 1}})
        return draft(), "local-test"
    monkeypatch.setattr(editor, "infer", generate_while_editing)
    result = client.post(path + "/metadata/generate", json={"revision": 0, "transcript_revision": 0})
    assert result.status_code == 409 and "metadata_draft" not in jobs.find_one()


def test_model_requires_local_artifacts_and_valid_citations(studio, monkeypatch):
    _, _, _, job, _ = studio
    monkeypatch.setenv("OLLAMA_BASE_URL", "https://remote.example")
    with pytest.raises(HTTPException, match="loopback"):
        editor.provider_request("/api/show", {}, 1)
    calls = []
    def cloud(path, body, timeout):
        calls.append(path)
        return {"remote_model": "external", "remote_host": "example.com"}
    monkeypatch.setattr(editor, "provider_request", cloud)
    with pytest.raises(HTTPException, match="Cloud-backed"):
        editor.infer(job)
    assert calls == ["/api/show"]
    def invalid_citation(path, body, timeout):
        if path == "/api/show":
            return {"model_info": {"general.architecture": "llama"}}
        assert body["stream"] is False and body["format"]["type"] == "object"
        answer = draft()
        answer.titles[0].segment_index = 99
        return {"message": {"content": answer.model_dump_json()}}
    monkeypatch.setattr(editor, "provider_request", invalid_citation)
    with pytest.raises(HTTPException, match="unknown passage"):
        editor.infer(job)


def test_generated_reasons_must_explain_a_quote_from_the_specific_passage():
    passage = "Keep the nearest pine trees in the frame for scale."
    assert editor.grounded_reason("The phrase ‘pine trees’ suggests a familiar reference for scale.", passage)
    assert editor.grounded_reason("The word 'basil' names the herb.", "Basil.")
    assert not editor.grounded_reason("The word 'bas' names the herb.", "Basil.")
    assert not editor.grounded_reason("Concrete detail in the passage supporting the wording.", passage)
    assert not editor.grounded_reason("The words 'alpine ridge' help photographers show scale.", passage)
    assert not editor.grounded_reason("'pine trees'", passage)
    generic = draft()
    with pytest.raises(HTTPException, match="verified quote"):
        editor.validate_items(generic, {0: passage, 1: "Trees cover the ridge."}, generated=True)


def test_model_repairs_missing_source_quotes_once(studio, monkeypatch):
    _, _, _, report, _ = studio
    calls = []
    def respond(route, body, timeout):
        if route == "/api/show":
            return {"model_info": {"general.architecture": "llama"}}
        calls.append(len(body["messages"]))
        output = draft()
        if len(calls) == 2:
            for group in editor.Draft.model_fields:
                for item in getattr(output, group):
                    phrase = "basil roots" if item.segment_index == 0 else "mint roots"
                    item.reason = f"The phrase '{phrase}' identifies the specific herb."
        return {"message": {"content": output.model_dump_json()}}
    monkeypatch.setattr(editor, "provider_request", respond)
    result, _ = editor.infer(report)
    assert calls == [2, 4] and "basil roots" in result.titles[0].reason


def test_unique_quote_realignment_and_bounded_partial_results():
    source = {0: "Basil roots grow. Fresh herbs thrive.", 1: "Mint leaves spread.", 2: "Fresh herbs thrive."}
    out = draft()
    for group in editor.Draft.model_fields:
        for item in getattr(out, group):
            item.reason = "The phrase 'mint leaves' identifies the featured herb."
            item.segment_index = 0
    out.chapters[1].reason = "The words 'fresh herbs' describe the featured plants."
    out.chapters[1].segment_index = 1
    response = {"message": {"content": out.model_dump_json()}}
    with pytest.raises(HTTPException, match="Missing verified"):
        editor.parse_suggestions(response, source)
    saved = editor.parse_suggestions(response, source, allow_partial=True)
    assert saved.titles[0].segment_index == 1
    assert len(saved.chapters) == 1 and saved._omitted_count == 1
    out.descriptions[0].reason = "Concrete detail in the passage supporting the wording."
    with pytest.raises(HTTPException, match="No descriptions"):
        editor.parse_suggestions({"message": {"content": out.model_dump_json()}}, source, allow_partial=True)
