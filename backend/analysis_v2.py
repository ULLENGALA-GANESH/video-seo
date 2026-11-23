"""Private analysis reports and a MongoDB-backed, restartable processing queue."""

import logging
import os
import shutil
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pymongo import ASCENDING, DESCENDING, ReturnDocument
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException

from notifications import notify
from account_v2 import current_user, get_db
from analysis_media import AnalysisError, SUPPORTED_MEDIA, media_root, transcribe_media
from analysis_text import summarize, transcript_segments


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/analyses", tags=["Analyses"])
MAX_FILE = 100 * 1024 * 1024
MAX_BODY = MAX_FILE + 64 * 1024
MAX_TEXT = 60_000
LEASE = timedelta(minutes=5)
MEDIA_MARKER = ".framewise-upload"
PUBLIC_SUMMARY = (
    "title", "source_type", "status", "stage", "progress", "error", "created_at",
    "updated_at", "word_count", "keyword_count", "duration_seconds",
)


def utcnow():
    return datetime.now(timezone.utc)


def ensure_indexes(db):
    jobs = db.analyses_v2
    jobs.create_index([("user_id", ASCENDING), ("_id", DESCENDING)])
    jobs.create_index([("status", ASCENDING), ("lease_until", ASCENDING), ("_id", ASCENDING)])


def build_job(user_id, title, source_type, source_text=""):
    now = utcnow()
    return {
        "_id": ObjectId(), "user_id": user_id, "title": title, "source_type": source_type,
        "source_text": source_text, "status": "queued", "stage": "Queued", "progress": 0,
        "created_at": now, "updated_at": now, "error": None,
    }


def timestamp(value):
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def public_report(job, detail=False):
    result = {key: job.get(key) for key in PUBLIC_SUMMARY}
    result.update(
        id=str(job["_id"]), created_at=timestamp(job["created_at"]),
        updated_at=timestamp(job["updated_at"]), word_count=job.get("word_count", 0),
        keyword_count=job.get("keyword_count", 0),
    )
    if detail:
        result.update(
            transcript=job.get("transcript", ""), transcript_revision=job.get("transcript_revision", 0), segments=job.get("segments", []),
            keywords=job.get("keywords", []), speech_coverage=job.get("speech_coverage"),
        )
    return result


def owned_id(report_id):
    if not ObjectId.is_valid(report_id):
        raise HTTPException(404, "Analysis not found.")
    return ObjectId(report_id)


@router.get("")
def list_analyses(
    limit: int = Query(20, ge=1, le=50), cursor: str | None = None, user=Depends(current_user),
    db=Depends(get_db),
):
    criteria = {"user_id": user["_id"]}
    if cursor is not None:
        if not ObjectId.is_valid(cursor):
            raise HTTPException(422, "Invalid history cursor.")
        criteria["_id"] = {"$lt": ObjectId(cursor)}
    rows = list(db.analyses_v2.find(criteria, dict.fromkeys(PUBLIC_SUMMARY, 1)).sort("_id", -1).limit(limit + 1))
    return {
        "items": [public_report(row) for row in rows[:limit]],
        "next_cursor": str(rows[limit - 1]["_id"]) if len(rows) > limit else None,
    }


def store_submission(form, user_id, db):
    allowed = {"title", "transcript", "file", "consent"}
    if any(k not in allowed or len(form.getlist(k)) != 1 for k in form):
        raise HTTPException(422, "Unexpected or repeated submission field.")
    title, transcript, upload = form.get("title", ""), form.get("transcript", ""), form.get("file")
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 140:
        raise HTTPException(422, "Enter a title between 1 and 140 characters.")
    if not isinstance(transcript, str) or len(transcript) > MAX_TEXT:
        raise HTTPException(422, "The transcript must be at most 60,000 characters.")
    transcript = transcript.strip()
    if bool(transcript) == (upload is not None):
        raise HTTPException(422, "Submit either a transcript or one media file.")
    if transcript and not any(character.isalpha() for character in transcript):
        raise HTTPException(422, "The transcript needs spoken words to analyze.")
    job = build_job(user_id, title.strip(), "transcript" if transcript else "video", transcript)
    if upload is not None:
        if not isinstance(upload, UploadFile):
            raise HTTPException(422, "Choose a media file.")
        if str(form.get("consent", "")).lower() not in {"true", "on", "1"}:
            raise HTTPException(422, "Consent is required to send audio to Google for transcription.")
        suffix = Path(upload.filename or "").suffix.lower()
        if suffix not in SUPPORTED_MEDIA:
            raise HTTPException(415, "Unsupported media format.")
        folder = media_root() / str(job["_id"])
        folder.mkdir(parents=True, exist_ok=False)
        try:
            (folder / MEDIA_MARKER).touch(exist_ok=False)
            total = 0
            with (folder / ("source" + suffix)).open("xb") as target:
                while chunk := upload.file.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_FILE:
                        raise HTTPException(413, "The file exceeds 100 MiB.")
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            if not total:
                raise HTTPException(422, "The uploaded file is empty.")
        except Exception:
            shutil.rmtree(folder)
            raise
        job["media_suffix"] = suffix
        job["speech_consent_at"] = utcnow()
    # Insert only after the complete source exists. Preserve it on an ambiguous DB write
    # so an acknowledged job never loses its input.
    db.analyses_v2.insert_one(job)
    return public_report(job, detail=True)


SUBMISSION_SCHEMA = {
    "requestBody": {"required": True, "content": {"multipart/form-data": {"schema": {
        "type": "object", "required": ["title"],
        "properties": {
            "title": {"type": "string", "minLength": 1, "maxLength": 140},
            "transcript": {"type": "string", "maxLength": MAX_TEXT},
            "file": {"type": "string", "format": "binary"},
            "consent": {"type": "boolean", "description": "Required for media transcription."},
        },
        "oneOf": [{"required": ["transcript"]}, {"required": ["file", "consent"]}],
    }}}},
}


@router.post("", status_code=202, openapi_extra=SUBMISSION_SCHEMA)
async def create_analysis(request: Request, user=Depends(current_user), db=Depends(get_db)):
    if request.headers.get("content-type", "").partition(";")[0].strip().lower() != "multipart/form-data":
        raise HTTPException(415, "Use a multipart form to submit an analysis.")
    size = request.headers.get("content-length", "")
    if size.isdigit() and int(size) > MAX_BODY:
        raise HTTPException(413, "The upload exceeds 100 MiB.")
    received = 0

    async def bounded_receive():
        nonlocal received
        event = await request.receive()
        received += len(event.get("body", b""))
        if received > MAX_BODY:
            # The multipart parser closes its own spooled files on MultiPartException.
            raise MultiPartException("Upload exceeds the size limit.")
        return event

    limited = Request(request.scope, receive=bounded_receive)
    try:
        async with limited.form(max_files=1, max_fields=3) as form:
            return await run_in_threadpool(store_submission, form, user["_id"], db)
    except Exception as exc:
        if received > MAX_BODY:
            raise HTTPException(413, "The upload exceeds 100 MiB.") from exc
        raise


@router.get("/{report_id}")
def get_analysis(report_id: str, user=Depends(current_user), db=Depends(get_db)):
    job = db.analyses_v2.find_one({"_id": owned_id(report_id), "user_id": user["_id"]})
    if not job:
        raise HTTPException(404, "Analysis not found.")
    return public_report(job, detail=True)


@router.post("/{report_id}/retry", status_code=202)
def retry_analysis(report_id: str, user=Depends(current_user), db=Depends(get_db)):
    jobs = db.analyses_v2
    criteria = {"_id": owned_id(report_id), "user_id": user["_id"]}
    job = jobs.find_one_and_update(
        {**criteria, "status": "failed"},
        {"$set": {"status": "queued", "stage": "Queued", "progress": 0, "error": None, "updated_at": utcnow()}},
        return_document=ReturnDocument.AFTER,
    )
    if not job:
        if not jobs.find_one(criteria, {"_id": 1}):
            raise HTTPException(404, "Analysis not found.")
        raise HTTPException(409, "Only failed analyses can be retried.")
    return public_report(job, detail=True)


class LeaseLost(Exception):
    pass


def claim_job(db):
    now = utcnow()
    return db.analyses_v2.find_one_and_update(
        {"$or": [{"status": "queued"}, {"status": "processing", "lease_until": {"$lte": now}}]},
        {"$set": {
            "status": "processing", "lease_id": uuid4().hex, "lease_until": now + LEASE,
            "updated_at": now, "stage": "Preparing analysis", "progress": 3, "error": None,
        }},
        sort=[("_id", ASCENDING)], return_document=ReturnDocument.AFTER,
    )


def process_claim(db, job):
    jobs = db.analyses_v2
    ownership = {"_id": job["_id"], "status": "processing", "lease_id": job["lease_id"]}
    release = {"lease_id": "", "lease_until": ""}

    def progress(stage, percent):
        now = utcnow()
        result = jobs.update_one(ownership, {"$set": {
            "stage": stage, "progress": percent, "updated_at": now, "lease_until": now + LEASE,
        }})
        if not result.matched_count:
            raise LeaseLost()

    source_file = None
    try:
        if job["source_type"] == "video":
            if not job.get("speech_consent_at"):
                raise AnalysisError("Speech consent is missing. Submit the media again with consent.")
            source_file = media_root() / str(job["_id"]) / ("source" + job["media_suffix"])
            segments, duration, coverage = transcribe_media(source_file, progress)
        else:
            segments = transcript_segments(job["source_text"])
            duration = None
            coverage = None
        progress("Ranking keywords", 90)
        result = summarize(segments, job["title"])
        if job["source_type"] == "transcript":
            result["transcript"] = job["source_text"]
        result.update(status="completed", stage="Completed", progress=100, duration_seconds=duration,
                      speech_coverage=coverage,
                      updated_at=utcnow(), error=None)
        written = jobs.update_one(ownership, {"$set": result, "$unset": release})
    except LeaseLost:
        return
    except Exception as exc:
        if isinstance(exc, AnalysisError):
            message = str(exc)
        else:
            logger.exception("Analysis processing failed for %s", job["_id"])
            message = "Analysis could not be completed. Retry this report."
        jobs.update_one(ownership, {
            "$set": {"status": "failed", "stage": "Failed", "progress": 0, "error": message, "updated_at": utcnow()},
            "$unset": release,
        })
        return
    if written.matched_count:
        notify(db, job["user_id"], "analysis:" + str(job["_id"]), "Analysis completed", job["title"])
    if written.matched_count and source_file:
        # Other workers may still be releasing a former lease's temporary files.
        try:
            source_file.unlink(missing_ok=True)
            marker = source_file.parent / MEDIA_MARKER
            if list(source_file.parent.iterdir()) == [marker]:
                marker.unlink(missing_ok=True)
                source_file.parent.rmdir()
        except OSError:
            logger.warning("Deferred media directory cleanup for %s", job["_id"])


def cleanup_media(db, now=None):
    """Collect abandoned uploads and decode artifacts after a one-day grace period."""
    root = media_root()
    if not root.is_dir():
        return
    cutoff = (now or utcnow()).timestamp() - 24 * 3600
    for folder in root.iterdir():
        if not ObjectId.is_valid(folder.name) or folder.is_symlink() or not folder.is_dir():
            continue
        try:
            marker = folder / MEDIA_MARKER
            if not marker.is_file() or marker.stat().st_mtime > cutoff:
                continue
            job = db.analyses_v2.find_one({"_id": ObjectId(folder.name)}, {"status": 1})
            if job and job["status"] == "processing":
                continue
            for entry in folder.glob("decode-*"):
                if entry.is_dir() and not entry.is_symlink() and entry.stat().st_mtime < cutoff:
                    (entry / "speech.wav").unlink(missing_ok=True)
                    entry.rmdir()
            if job is None or job["status"] == "completed":
                for suffix in SUPPORTED_MEDIA:
                    (folder / ("source" + suffix)).unlink(missing_ok=True)
                if list(folder.iterdir()) == [marker]:
                    marker.unlink(missing_ok=True)
                    folder.rmdir()
        except FileNotFoundError:
            continue  # Another worker completed the same cleanup.
        except OSError:
            logger.warning("Could not clean media directory for %s", folder.name)


def run_worker(stop_event=None):
    stop_event = stop_event or threading.Event()
    indexed = False
    next_cleanup = 0
    while not stop_event.is_set():
        try:
            db = get_db()
            if not indexed:
                ensure_indexes(db)
                indexed = True
            if time.monotonic() >= next_cleanup:
                cleanup_media(db)
                next_cleanup = time.monotonic() + 3600
            job = claim_job(db)
            if job:
                process_claim(db, job)
                continue
        except Exception:
            logger.exception("Analysis worker could not access its queue")
            stop_event.wait(4)
        stop_event.wait(1)


def start_worker():
    stop_event = threading.Event()
    worker = threading.Thread(target=run_worker, args=(stop_event,), name="analysis-worker", daemon=True)
    worker.start()
    return worker, stop_event
