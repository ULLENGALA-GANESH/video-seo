from bson import ObjectId
from fastapi import HTTPException
import mongomock
import pytest

from notifications import mark_read, notify, recent


def test_notifications_are_private_and_retries_are_idempotent():
    db = mongomock.MongoClient().studio
    owner, other = {"_id": ObjectId()}, {"_id": ObjectId()}
    report_id = str(ObjectId())
    event = "analysis:" + report_id
    notify(db, owner["_id"], event, "Analysis completed", "Garden notes")
    notify(db, other["_id"], event, "Analysis completed", "Another report")

    notice, = recent(30, owner, db)
    assert notice["report_id"] == report_id
    assert notice["message"] == "Garden notes"
    assert notice["read"] is False
    with pytest.raises(HTTPException) as error:
        mark_read(notice["id"], other, db)
    assert error.value.status_code == 404

    mark_read(notice["id"], owner, db)
    notify(db, owner["_id"], event, "Analysis completed", "Duplicate")
    assert recent(30, owner, db) == [{**notice, "read": True}]
    assert recent(30, other, db)[0]["read"] is False
