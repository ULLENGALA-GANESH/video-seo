"""Preview or import saved v1 transcripts into the private analysis queue."""
import argparse
from collections import Counter
from datetime import datetime, timezone

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from account_v2 import get_db
from analysis_v2 import MAX_TEXT, build_job


def import_transcripts(db, apply=False):
    """Keep v1 documents intact and preserve ownership and original report IDs."""
    counts = Counter()
    fields = {"title": 1, "user_id": 1, "extracted_text": 1, "created_at": 1}
    for video in db.videos.find({}, fields):
        text = video.get("extracted_text")
        if not isinstance(text, str) or not text.strip():
            counts["missing transcript"] += 1
            continue
        if text.strip().casefold().startswith(("this is a placeholder text for videos where text extraction failed", "mock transcription text")):
            counts["placeholder"] += 1
            continue
        owner = video.get("user_id")
        if not ObjectId.is_valid(owner) or not isinstance(video["_id"], ObjectId):
            counts["unsupported ID"] += 1
            continue
        owner = ObjectId(owner)
        if not db.users.find_one({"_id": owner}, {"_id": 1}):
            counts["missing owner"] += 1
            continue
        if len(text) > MAX_TEXT:
            counts["over text limit"] += 1
            continue
        if db.analyses_v2.find_one({"_id": video["_id"]}, {"_id": 1}):
            counts["already present"] += 1
            continue
        if not apply:
            counts["eligible"] += 1
            continue
        title = str(video.get("title") or "Imported video").strip()[:140] or "Imported video"
        job = build_job(owner, title, "transcript", text.strip())
        job["_id"] = video["_id"]
        job["legacy_video_id"] = video["_id"]
        created = video.get("created_at")
        if isinstance(created, datetime):
            job["created_at"] = created if created.tzinfo else created.replace(tzinfo=timezone.utc)
        try:
            db.analyses_v2.insert_one(job)
            counts["queued"] += 1
        except DuplicateKeyError:
            counts["already present"] += 1
    return counts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Insert eligible transcripts for re-analysis")
    args = parser.parse_args()
    print("Import results" if args.apply else "Import preview")
    print(dict(import_transcripts(get_db(), apply=args.apply)))
