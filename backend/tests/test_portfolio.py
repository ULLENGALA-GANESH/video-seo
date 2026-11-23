import json
from pathlib import Path

import mongomock
from bson import ObjectId

from analysis_text import summarize
from import_legacy import import_transcripts


def test_sample_evidence_matches_current_extractor():
    sample = Path(__file__).resolve().parents[2] / "frontend/public/sample-report.json"
    report = json.loads(sample.read_text(encoding="utf-8"))
    calculated = summarize(report["segments"], report["title"])
    for field in ("keywords", "word_count", "keyword_count", "transcript"):
        assert report[field] == calculated[field]
    assert report["segments"][0]["start_seconds"] == 0
    assert report["segments"][-1]["end_seconds"] == report["duration_seconds"]


def test_legacy_import_is_private_repeatable_and_preserves_sources():
    db = mongomock.MongoClient().studio
    owner = ObjectId()
    db.users.insert_one({"_id": owner})
    old = {"_id": ObjectId(), "user_id": str(owner), "title": "Morning light", "extracted_text": "Side light reaches the ridge."}
    db.videos.insert_many([old, {"_id": ObjectId(), "user_id": str(owner), "extracted_text": "Mock transcription text"}])
    assert import_transcripts(db) == {"eligible": 1, "placeholder": 1}
    assert db.analyses_v2.count_documents({}) == 0
    assert import_transcripts(db, apply=True)["queued"] == 1
    assert import_transcripts(db, apply=True)["already present"] == 1
    job = db.analyses_v2.find_one({"_id": old["_id"]})
    assert job["user_id"] == owner and job["source_text"] == old["extracted_text"]
    assert db.videos.find_one({"_id": old["_id"]}) == old
