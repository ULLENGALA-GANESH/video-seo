import wave
from datetime import timedelta

import mongomock
import pytest
import speech_recognition as sr
from bson import ObjectId
from fastapi import FastAPI
from fastapi.testclient import TestClient

import analysis_v2 as api
from analysis_media import AnalysisError, MAX_SECONDS, parse_google_results, pause_boundary, recognize_audio, transcribe_media
from analysis_text import rank_keywords, transcript_segments


@pytest.fixture
def studio(monkeypatch, tmp_path):
    database = mongomock.MongoClient().studio
    owner = {"_id": ObjectId()}
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.get_db] = lambda: database
    app.dependency_overrides[api.current_user] = lambda: owner
    monkeypatch.setenv("MEDIA_ROOT", str(tmp_path))
    with TestClient(app) as client:
        yield client, database, owner


def new_transcript(client, text="Urban gardens provide shade. Urban gardens cool streets."):
    return client.post("/api/analyses", files={
        "title": (None, "Urban gardens"), "transcript": (None, text),
    })


def test_report_is_private_and_get_is_read_only(studio):
    client, db, owner = studio
    response = new_transcript(client)
    assert response.status_code == 202, response.text
    report_id = response.json()["id"]
    report_url = f"/api/analyses/{report_id}"
    job = api.claim_job(db)
    assert api.claim_job(db) is None
    api.process_claim(db, job)
    report = client.get(report_url).json()
    assert report["status"] == "completed"
    assert report["keywords"][0]["phrase"] == "urban gardens"
    assert report["keywords"][0]["occurrences"] == 2
    assert report["keywords"][0]["evidence"] in report["transcript"]
    assert report["keywords"][0]["start_seconds"] is None
    assert report["word_count"] == 8
    assert client.get(report_url).json() == report
    assert db.analyses_v2.count_documents({}) == 1
    assert client.post(report_url + "/retry").status_code == 409
    owner["_id"] = ObjectId()
    assert client.get(report_url).status_code == 404
    assert client.post(report_url + "/retry").status_code == 404
    assert client.get("/api/analyses").json()["items"] == []


def test_history_is_paginated_and_returns_only_summaries(studio):
    client, _, _ = studio
    ids = [new_transcript(client).json()["id"] for _ in range(3)]
    page = client.get("/api/analyses?limit=2").json()
    assert [report["id"] for report in page["items"]] == ids[:0:-1]
    assert "transcript" not in page["items"][0]
    rest = client.get("/api/analyses", params={"cursor": page["next_cursor"]}).json()
    assert [report["id"] for report in rest["items"]] == ids[:1]
    assert rest["next_cursor"] is None
    assert client.get("/api/analyses?cursor=invalid").status_code == 422
    assert client.get("/api/analyses/invalid").status_code == 404


def test_expired_worker_cannot_overwrite_new_owner(studio):
    client, db, _ = studio
    new_transcript(client)
    stale = api.claim_job(db)
    db.analyses_v2.update_one({"_id": stale["_id"]}, {"$set": {"lease_until": api.utcnow() - timedelta(seconds=1)}})
    fresh = api.claim_job(db)
    assert stale["lease_id"] != fresh["lease_id"]
    api.process_claim(db, stale)
    assert db.analyses_v2.find_one()["lease_id"] == fresh["lease_id"]
    api.process_claim(db, fresh)
    assert db.analyses_v2.find_one()["status"] == "completed"


def test_file_requires_consent_and_valid_size(studio, monkeypatch, tmp_path):
    client, db, _ = studio
    fields = {"title": (None, "A clip"), "file": ("my clip.mp4", b"12345")}
    assert client.post("/api/analyses", files=fields).status_code == 422
    fields["consent"] = (None, "true")
    monkeypatch.setattr(api, "MAX_FILE", 4)
    assert client.post("/api/analyses", files=fields).status_code == 413
    assert list(tmp_path.iterdir()) == []
    fields["file"] = ("test.txt", b"1234")
    assert client.post("/api/analyses", files=fields).status_code == 415
    assert db.analyses_v2.count_documents({}) == 0


def test_chunked_body_limit_is_enforced_without_content_length(studio, monkeypatch):
    client, db, _ = studio
    monkeypatch.setattr(api, "MAX_BODY", 180)
    body = (b"--abc\r\nContent-Disposition: form-data; name=\"transcript\"\r\n\r\n" +
            b"x" * 250 + b"\r\n--abc--\r\n")
    response = client.post("/api/analyses", content=iter([body[:110], body[110:]]),
                           headers={"Content-Type": "multipart/form-data; boundary=abc"})
    assert response.status_code == 413
    assert db.analyses_v2.count_documents({}) == 0


def test_failed_media_is_retryable_and_cleaned_after_success(studio, monkeypatch, tmp_path):
    client, db, _ = studio
    fields = {"title": (None, "Urban gardens"), "file": ("clip.wav", b"audio"), "consent": (None, "true")}
    first, second = [client.post("/api/analyses", files=fields).json()["id"] for _ in range(2)]
    assert (tmp_path / first / "source.wav").read_bytes() == (tmp_path / second / "source.wav").read_bytes()
    def unavailable(source, progress):
        raise AnalysisError("Speech service temporarily unavailable.")

    monkeypatch.setattr(api, "transcribe_media", unavailable)
    api.process_claim(db, api.claim_job(db))
    url = "/api/analyses/" + first
    assert client.get(url).json()["error"] == "Speech service temporarily unavailable."
    assert client.post(url + "/retry").json()["status"] == "queued"
    assert client.post(url + "/retry").status_code == 409
    monkeypatch.setattr(api, "transcribe_media", lambda source, progress: (
        [{"text": "Urban gardens grow.", "start_seconds": 0, "end_seconds": 2}], 2,
        {"partial": False, "total_chunks": 1, "recognized_chunks": 1, "unrecognized_ranges": []}))
    api.process_claim(db, api.claim_job(db))
    report = client.get(url).json()
    assert report["duration_seconds"] == 2
    assert report["speech_coverage"]["recognized_chunks"] == 1
    assert not (tmp_path / first).exists()
    assert (tmp_path / second).is_dir()


def write_wav(path, seconds, rate):
    with wave.open(str(path), "wb") as out:
        out.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        out.writeframes(bytes(seconds * rate * 2))


def test_ffmpeg_and_recognition_cover_all_95_seconds(tmp_path, monkeypatch):
    source = tmp_path / "clip.wav"
    write_wav(source, seconds=95, rate=8000)
    lengths = []
    def speech_stub(audio):
        lengths.append(len(audio.frame_data) / (audio.sample_rate * audio.sample_width))
        return "urban gardens"

    monkeypatch.setattr("analysis_media.recognize_chunk", speech_stub)
    segments, duration, coverage = transcribe_media(source, lambda *args: None)
    assert duration == 95
    assert lengths == [30, 30, 30, 5]
    assert [s["start_seconds"] for s in segments] == [0, 30, 60, 90]
    assert [s["end_seconds"] for s in segments] == [30, 60, 90, 95]
    assert coverage == {"partial": False, "recognized_chunks": 4, "total_chunks": 4, "unrecognized_ranges": []}
    assert list(tmp_path.iterdir()) == [source]


def test_overlong_audio_rejected_before_speech_call(tmp_path):
    source = tmp_path / "clip.wav"
    write_wav(source, seconds=MAX_SECONDS + 1, rate=100)
    with pytest.raises(AnalysisError, match="15-minute"):
        recognize_audio(source, lambda *args: None, recognize=lambda _: pytest.fail("Oversize audio submitted."))


def test_provider_parser_collects_multiple_utterances_and_ignores_interim_alternatives():
    response = '\n'.join([
        '{"result":[]}',
        '{"result":[{"alternative":[{"transcript":"interim"}],"final":false}],"result_index":0}',
        '{"result":[{"alternative":[{"transcript":"weak","confidence":0.1},{"transcript":"first","confidence":0.9}],"final":true},{"alternative":[{"transcript":"second"}],"final":true}],"result_index":0}',
        '{"result":[{"alternative":[{"transcript":"third"}],"final":true}],"result_index":2}',
    ])
    assert parse_google_results(response) == "first second third"
    with pytest.raises(sr.UnknownValueError):
        parse_google_results('{"result":[]}')


def test_pause_chunks_cover_every_frame_and_report_unrecognized_intervals(tmp_path):
    source = tmp_path / "clip.wav"
    rate = 1000
    # Constant signal around two natural pauses; ascending sections distinguish order.
    pcm = ((1280).to_bytes(2, "little") * 9000 + bytes(2 * 500)
           + (1500).to_bytes(2, "little") * 10000 + bytes(2 * 500)
           + (2000).to_bytes(2, "little") * 7000)
    with wave.open(str(source), "wb") as writer:
        writer.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        writer.writeframes(pcm)
    chunks = []
    def recognize(audio):
        chunks.append(audio.frame_data)
        if len(chunks) == 2:
            raise sr.UnknownValueError()
        return "spoken phrase"
    segments, duration, coverage = recognize_audio(source, lambda *args: None, recognize)
    assert b"".join(chunks) == pcm
    assert duration == 27 and len(chunks) == 3
    assert coverage == {
        "partial": True, "total_chunks": 3, "recognized_chunks": 2,
        "unrecognized_ranges": [{"start_seconds": 9.25, "end_seconds": 19.75}],
    }
    assert [seg["start_seconds"] for seg in segments] == [0, 19.75]


def test_long_pause_separates_short_utterances_before_the_ten_second_target():
    signal = (2000).to_bytes(2, "little")
    pcm = signal * 3500 + bytes(2 * 1000) + signal * 5000
    assert pause_boundary(pcm, rate=1000) == 4000


def test_keyword_excerpts_anchor_to_actual_occurrence():
    text = "the " * 100 + "alpine light appears."
    ranked = rank_keywords(transcript_segments(text), "Alpine light")
    phrase = next(k for k in ranked if k["phrase"] == "alpine light")
    assert phrase["evidence"] in text
    assert "alpine light" in phrase["evidence"]


def test_cleanup_recovers_abandoned_upload_but_preserves_retry_input(studio, tmp_path):
    client, db, _ = studio
    fields = {"title": (None, "Sample"), "file": ("sample.wav", b"audio"), "consent": (None, "true")}
    ids = [client.post("/api/analyses", files=fields).json()["id"] for _ in range(3)]
    db.analyses_v2.update_one({"_id": ObjectId(ids[0])}, {"$set": {"status": "failed"}})
    db.analyses_v2.update_one({"_id": ObjectId(ids[1])}, {"$set": {"status": "completed"}})
    db.analyses_v2.delete_one({"_id": ObjectId(ids[2])})
    unrelated = tmp_path / str(ObjectId())
    unrelated.mkdir()
    api.cleanup_media(db, api.utcnow() + timedelta(days=2))
    assert (tmp_path / ids[0] / "source.wav").read_bytes() == b"audio"
    assert not (tmp_path / ids[1]).exists()
    assert not (tmp_path / ids[2]).exists()
    assert unrelated.is_dir()
