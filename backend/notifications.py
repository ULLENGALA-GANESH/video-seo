"""Account-scoped events for completed work and confirmed channel actions."""
import hashlib
import logging
from functools import lru_cache
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pymongo.errors import PyMongoError

from account_v2 import current_user, get_db


router = APIRouter(prefix="/api/notifications", tags=["Notifications"])
logger = logging.getLogger(__name__)


@lru_cache(maxsize=2)
def ensure_indexes(db):
    db.activity.create_index([("user_id", 1), ("created_at", -1)])


def notify(db, user_id, event_id, title, message):
    key = hashlib.sha256(f"{user_id}/{event_id}".encode()).hexdigest()
    try:
        db.activity.update_one({"_id": key}, {"$setOnInsert": {
            "user_id": user_id, "event_id": event_id, "title": title, "message": message,
            "created_at": datetime.now(timezone.utc), "read": False,
        }}, upsert=True)
    except PyMongoError:
        logger.warning("Activity notification could not be recorded")


@router.get("")
def recent(limit: int = Query(30, ge=1, le=100), user=Depends(current_user), db=Depends(get_db)):
    ensure_indexes(db)
    rows = db.activity.find({"user_id": user["_id"]}).sort("created_at", -1).limit(limit)
    return [{"id": row["_id"], "title": row["title"], "message": row["message"],
             "created_at": row["created_at"], "read": row["read"],
             "report_id": row["event_id"].split(":", 1)[1] if row["event_id"].startswith("analysis:") else None}
            for row in rows]


@router.post("/{notice_id}/read")
def mark_read(notice_id: str, user=Depends(current_user), db=Depends(get_db)):
    result = db.activity.update_one({"_id": notice_id, "user_id": user["_id"]}, {"$set": {"read": True}})
    if not result.matched_count:
        raise HTTPException(404, "Notification not found.")
    return {"read": True}
