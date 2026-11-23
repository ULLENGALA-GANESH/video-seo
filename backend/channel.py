"""YouTube OAuth, owner-only channel reports, metadata and resumable uploads."""

import base64
import hashlib
import json
import os
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request as GoogleRequest, build_opener
from uuid import UUID

from bson import ObjectId
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pymongo import ReturnDocument
from starlette.concurrency import run_in_threadpool

from account_v2 import COOKIE_NAME, current_user, get_db, session_key
from notifications import notify


router = APIRouter(prefix="/api/channel", tags=["YouTube channel"])
DATA = "https://www.googleapis.com/youtube/v3/"
TOKEN = "https://oauth2.googleapis.com/token"
SCOPES = tuple(
    "https://www.googleapis.com/auth/" + name
    for name in ("youtube.readonly", "yt-analytics.readonly", "youtube.force-ssl")
)
CHUNK = 4 * 1024 * 1024
MAX_UPLOAD = 100 * 1024 * 1024


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def transport(method, url, *, headers=None, body=None):
    req = GoogleRequest(url, data=body, headers=headers or {}, method=method)
    try:
        try:
            reply = build_opener(NoRedirect()).open(req, timeout=35)
        except HTTPError as exc:
            reply = exc
        with reply:
            raw = reply.read(2 * 1024 * 1024)
            try:
                document = json.loads(raw) if raw else {}
            except (UnicodeError, ValueError):
                document = {}
            return reply.status, {k.lower(): v for k, v in reply.headers.items()}, document
    except (URLError, TimeoutError, OSError) as exc:
        raise HTTPException(503, "Google could not be reached. Try again.") from exc


def checked(response):
    status, _, doc = response
    if 200 <= status < 300:
        return doc
    error = doc.get("error", {}) if isinstance(doc, dict) else {}
    reasons = {e.get("reason") for e in error.get("errors", [])} if isinstance(error, dict) else set()
    if status == 429 or reasons & {
        "quotaExceeded",
        "dailyLimitExceeded",
        "rateLimitExceeded",
        "uploadLimitExceeded",
        "userRateLimitExceeded",
    }:
        raise HTTPException(429, "YouTube's quota or upload limit has been reached. Try again later.")
    if status == 401 or error == "invalid_grant":
        raise HTTPException(409, "YouTube authorization has expired. Reconnect your channel.")
    if status == 403:
        raise HTTPException(
            403, "Google denied this action. Check the channel permissions and enabled APIs."
        )
    if status in {404, 410}:
        raise HTTPException(404, "This YouTube resource or upload session is no longer available.")
    if status == 412:
        raise HTTPException(409, "The video changed on YouTube. Reload it before saving.")
    if status == 400:
        raise HTTPException(
            422, "Google rejected the request. Check your video details and channel settings."
        )
    raise HTTPException(503, "YouTube is temporarily unavailable. Try again.")


def config():
    values = [
        os.getenv(k, "").strip()
        for k in (
            "YOUTUBE_CLIENT_ID",
            "YOUTUBE_CLIENT_SECRET",
            "YOUTUBE_REDIRECT_URI",
            "YOUTUBE_TOKEN_KEY",
        )
    ]
    try:
        redirect = urlsplit(values[2])
    except ValueError as exc:
        raise HTTPException(503, "The YouTube callback URL needs configuration.") from exc
    local = redirect.hostname in {"localhost", "127.0.0.1", "::1"}
    if (
        not all(values)
        or not redirect.hostname
        or redirect.username
        or redirect.password
        or not (redirect.scheme == "https" or local and redirect.scheme == "http")
        or (redirect.path != "/api/channel/oauth/callback" or redirect.query or redirect.fragment)
    ):
        raise HTTPException(503, "YouTube connection is unavailable on this installation.")
    try:
        return *values[:3], Fernet(values[3].encode())
    except (ValueError, TypeError) as exc:
        raise HTTPException(503, "The channel encryption key needs configuration.") from exc


def decrypt(cipher, value):
    try:
        return cipher.decrypt(value.encode()).decode()
    except (InvalidToken, ValueError):
        raise HTTPException(
            503, "Stored channel credentials cannot be opened. Check the encryption key."
        )


def form_post(url, values):
    return transport(
        "POST",
        url,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        body=urlencode(values).encode(),
    )


def google_api(token, path, params, *, method="GET", body=None, etag=None):
    headers = {"Authorization": "Bearer " + token}
    if etag:
        headers["If-Match"] = etag
    if body is not None:
        headers["Content-Type"] = "application/json"
    return checked(
        transport(
            method,
            path + "?" + urlencode(params),
            headers=headers,
            body=json.dumps(body).encode() if body is not None else None,
        )
    )


def channel_info(token):
    data = google_api(
        token, DATA + "channels", {"part": "snippet,contentDetails,statistics", "mine": "true"}
    )
    if not data.get("items"):
        raise HTTPException(422, "This Google account has no YouTube channel.")
    item = data["items"][0]
    stats = item.get("statistics", {})
    return {
        "id": item["id"],
        "title": item["snippet"]["title"],
        "uploads": item["contentDetails"]["relatedPlaylists"]["uploads"],
        "views": int(stats["viewCount"]) if "viewCount" in stats else None,
        "subscribers": int(stats["subscriberCount"])
        if "subscriberCount" in stats and not stats.get("hiddenSubscriberCount")
        else None,
        "videos": int(stats["videoCount"]) if "videoCount" in stats else None,
    }


def connected(db, user_id):
    link = db.youtube_channels.find_one({"_id": user_id})
    if not link:
        raise HTTPException(409, "Connect a YouTube channel first.")
    client, secret, _, cipher = config()
    if link["expires"] <= datetime.now(UTC).timestamp() + 60:
        response = checked(
            form_post(
                TOKEN,
                {
                    "grant_type": "refresh_token",
                    "client_id": client,
                    "client_secret": secret,
                    "refresh_token": decrypt(cipher, link["refresh"]),
                },
            )
        )
        updated = {
            "access": cipher.encrypt(response["access_token"].encode()).decode(),
            "expires": datetime.now(UTC).timestamp() + response["expires_in"],
        }
        if response.get("refresh_token"):
            updated["refresh"] = cipher.encrypt(response["refresh_token"].encode()).decode()
        if not db.youtube_channels.update_one(
            {"_id": user_id, "generation": link["generation"]}, {"$set": updated}
        ).matched_count:
            raise HTTPException(409, "The channel connection changed. Reload and try again.")
        link.update(updated)
    return link, decrypt(cipher, link["access"])


@router.get("")
def status(user=Depends(current_user), db=Depends(get_db)):
    try:
        config()
        configured = True
    except HTTPException:
        configured = False
    link = db.youtube_channels.find_one({"_id": user["_id"]}, {"channel": 1})
    return {"configured": configured, "channel": link["channel"] if link else None}


@router.post("/oauth/start")
def start(request: Request, report: str | None = None, user=Depends(current_user), db=Depends(get_db)):
    client, _, redirect, _ = config()
    if report is not None and not ObjectId.is_valid(report):
        raise HTTPException(422, "Invalid source report.")
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    db.youtube_oauth.create_index("expires_at", expireAfterSeconds=0)
    db.youtube_oauth.update_one(
        {"_id": user["_id"]},
        {
            "$set": {
                "state": hashlib.sha256(state.encode()).hexdigest(),
                "session": session_key(request.cookies.get(COOKIE_NAME, "")),
                "verifier": verifier,
                "report": report,
                "expires_at": datetime.now(UTC) + timedelta(minutes=10),
            }
        },
        upsert=True,
    )
    query = {
        "client_id": client,
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "state": state,
        "access_type": "offline",
        "prompt": "consent",
        "code_challenge_method": "S256",
        "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("="),
    }
    return {"url": "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(query)}


@router.get("/oauth/callback")
def callback(
    request: Request,
    state: str = "",
    code: str = "",
    error: str = "",
    user=Depends(current_user),
    db=Depends(get_db),
):
    pending = db.youtube_oauth.find_one_and_delete(
        {
            "_id": user["_id"],
            "state": hashlib.sha256(state.encode()).hexdigest(),
            "session": session_key(request.cookies.get(COOKIE_NAME, "")),
            "expires_at": {"$gt": datetime.now(UTC)},
        }
    )
    if not pending:
        return RedirectResponse("/channel?connection=expired", status_code=303)

    def return_to_channel(outcome):
        params = {"connection": outcome}
        if pending.get("report"):
            params["report"] = pending["report"]
        return RedirectResponse("/channel?" + urlencode(params), status_code=303)

    if error or not code:
        return return_to_channel("declined")
    client, secret, redirect, cipher = config()
    try:
        tokens = checked(
            form_post(
                TOKEN,
                {
                    "client_id": client,
                    "client_secret": secret,
                    "redirect_uri": redirect,
                    "code": code,
                    "code_verifier": pending["verifier"],
                    "grant_type": "authorization_code",
                },
            )
        )
        if not tokens.get("refresh_token") or not set(SCOPES).issubset(tokens.get("scope", "").split()):
            return return_to_channel("permissions")
        channel = channel_info(tokens["access_token"])
        db.youtube_channels.replace_one(
            {"_id": user["_id"]},
            {
                "_id": user["_id"],
                "channel": channel,
                "generation": secrets.token_hex(16),
                "access": cipher.encrypt(tokens["access_token"].encode()).decode(),
                "refresh": cipher.encrypt(tokens["refresh_token"].encode()).decode(),
                "expires": datetime.now(UTC).timestamp() + tokens["expires_in"],
            },
            upsert=True,
        )
    except HTTPException as exc:
        reason = {422: "channel", 429: "quota", 403: "permissions"}.get(exc.status_code, "failed")
        return return_to_channel(reason)
    return return_to_channel("success")


@router.delete("")
def disconnect(user=Depends(current_user), db=Depends(get_db)):
    link = db.youtube_channels.find_one({"_id": user["_id"]})
    revoked = "already_disconnected"
    if link:
        try:
            _, _, _, cipher = config()
            result = form_post(
                "https://oauth2.googleapis.com/revoke", {"token": decrypt(cipher, link["refresh"])}
            )
            if result[0] == 400 and result[2].get("error") == "invalid_token":
                revoked = "already_revoked"
            else:
                checked(result)
                revoked = "confirmed"
        except HTTPException:
            revoked = "unconfirmed"
        db.youtube_channels.delete_one({"_id": user["_id"], "generation": link["generation"]})
    db.youtube_uploads.delete_many({"owner": user["_id"]})
    db.activity.delete_many({"user_id": user["_id"], "event_id": {"$regex": "^youtube:"}})
    db.youtube_oauth.delete_one({"_id": user["_id"]})
    return {"disconnected": True, "revocation": revoked}


@router.get("/analytics")
def analytics(user=Depends(current_user), db=Depends(get_db)):
    link, token = connected(db, user["_id"])
    channel = channel_info(token)
    if channel["id"] != link["channel"]["id"]:
        raise HTTPException(409, "Channel identity changed. Reconnect to continue.")
    end = datetime.now(UTC).date() - timedelta(days=1)
    start_date = end - timedelta(days=27)
    data = google_api(
        token,
        "https://youtubeanalytics.googleapis.com/v2/reports",
        {
            "ids": "channel==MINE",
            "startDate": start_date.isoformat(),
            "endDate": end.isoformat(),
            "dimensions": "day",
            "sort": "day",
            "metrics": "views,estimatedMinutesWatched,subscribersGained,subscribersLost",
        },
    )
    columns = [col["name"] for col in data.get("columnHeaders", [])]
    rows = [dict(zip(columns, values)) for values in data.get("rows", [])]
    updated = db.youtube_channels.update_one(
        {"_id": user["_id"], "generation": link["generation"]}, {"$set": {"channel": channel}}
    )
    if updated.matched_count:
        for metric, thresholds in {
            "views": (1000, 10000, 100000, 1000000),
            "subscribers": (100, 1000, 10000, 100000),
        }.items():
            before, after = link["channel"].get(metric), channel.get(metric)
            crossed = [
                v for v in thresholds if before is not None and after is not None and before < v <= after
            ]
            if crossed:
                count = max(crossed)
                notify(
                    db,
                    user["_id"],
                    f"youtube:{channel['id']}:{metric}:{count}",
                    "Channel milestone",
                    f"{channel['title']} reached {count:,} lifetime {metric}.",
                )
    return {"channel": channel, "start": str(start_date), "end": str(end), "rows": rows}


def video_details(video):
    meta = video["snippet"]
    return {
        "id": video["id"],
        "title": meta["title"],
        "description": meta.get("description", ""),
        "tags": meta.get("tags", []),
        "visibility": video.get("status", {}).get("privacyStatus"),
        "etag": video.get("etag", ""),
    }


@router.get("/videos")
def library(page: str = "", user=Depends(current_user), db=Depends(get_db)):
    link, token = connected(db, user["_id"])
    params = {"part": "contentDetails", "playlistId": link["channel"]["uploads"], "maxResults": 12}
    if page:
        params["pageToken"] = page[:512]
    playlist = google_api(token, DATA + "playlistItems", params)
    ids = [v["contentDetails"]["videoId"] for v in playlist.get("items", [])]
    result = (
        google_api(token, DATA + "videos", {"part": "snippet,status", "id": ",".join(ids)})
        if ids
        else {}
    )
    return {
        "items": [
            video_details(v)
            for v in result.get("items", [])
            if v["snippet"]["channelId"] == link["channel"]["id"]
        ],
        "next": playlist.get("nextPageToken"),
    }


class Metadata(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(max_length=5000)
    tags: list[str] = Field(max_length=60)
    confirmed: Literal[True]

    @field_validator("tags")
    @classmethod
    def tag_limit(cls, items):
        if (
            any(not tag or "<" in tag or ">" in tag for tag in items)
            or len(",".join(items)) + sum(2 for t in items if " " in t) > 500
        ):
            raise ValueError("Use fewer than 500 characters of YouTube tags.")
        return items

    @field_validator("title", "description")
    @classmethod
    def valid_text(cls, text):
        if "<" in text or ">" in text or len(text.encode()) > 5000:
            raise ValueError("YouTube text must fit 5,000 bytes and exclude angle brackets.")
        return text


class VideoEdit(Metadata):
    etag: str = Field(min_length=1, max_length=200)


@router.patch("/videos/{video_id}")
def edit(video_id: str, values: VideoEdit, user=Depends(current_user), db=Depends(get_db)):
    link, token = connected(db, user["_id"])
    doc = google_api(token, DATA + "videos", {"part": "snippet,status", "id": video_id})
    video = next(
        (
            v
            for v in doc.get("items", [])
            if v["id"] == video_id and v["snippet"]["channelId"] == link["channel"]["id"]
        ),
        None,
    )
    if not video:
        raise HTTPException(404, "Video not found in your channel.")
    if video["etag"] != values.etag:
        raise HTTPException(409, "The video changed on YouTube. Reload before saving.")
    original = video["snippet"]
    snippet = {key: original[key] for key in ("categoryId", "defaultLanguage") if key in original}
    snippet.update(title=values.title, description=values.description, tags=values.tags)
    result = google_api(
        token,
        DATA + "videos",
        {"part": "snippet"},
        method="PUT",
        body={"id": video_id, "snippet": snippet},
        etag=values.etag,
    )
    notify(
        db,
        user["_id"],
        f"youtube:edit:{video_id}:{result['etag']}",
        "Video metadata updated",
        values.title,
    )
    return video_details({**result, "status": video.get("status", {})})


class Upload(Metadata):
    request_id: UUID
    size: int = Field(gt=0, le=MAX_UPLOAD)
    mime: Literal["video/mp4", "video/quicktime", "video/webm", "video/x-matroska"]
    visibility: Literal["private", "unlisted", "public"] = "private"
    made_for_kids: bool
    report_id: str | None = None
    metadata_revision: int | None = Field(default=None, ge=0)
    transcript_revision: int | None = Field(default=None, ge=0)


def transfer_view(doc):
    return {
        "id": doc["request_id"],
        "offset": doc["offset"],
        "size": doc["size"],
        "video_id": doc.get("video_id"),
        "visibility": doc.get("actual_visibility"),
    }


def transfer_query(user_id, link, request_id):
    return {
        "_id": str(user_id) + ":" + str(request_id),
        "owner": user_id,
        "generation": link["generation"],
    }


@router.post("/uploads")
def begin(values: Upload, user=Depends(current_user), db=Depends(get_db)):
    link, token = connected(db, user["_id"])
    query = transfer_query(user["_id"], link, values.request_id)
    fingerprint = hashlib.sha256(values.model_dump_json().encode()).hexdigest()
    existing = db.youtube_uploads.find_one(query)
    if existing:
        if existing["fingerprint"] != fingerprint:
            raise HTTPException(409, "The existing upload has different settings.")
        return transfer_view(existing)
    if values.report_id:
        job = (
            db.analyses_v2.find_one({"_id": ObjectId(values.report_id), "user_id": user["_id"]})
            if ObjectId.is_valid(values.report_id)
            else None
        )
        if not job:
            raise HTTPException(404, "Analysis not found.")
        if (
            job.get("metadata_revision", 0) != values.metadata_revision
            or job.get("transcript_revision", 0) != values.transcript_revision
            or (job.get("metadata_draft") or {}).get("source_revision") != values.transcript_revision
        ):
            raise HTTPException(409, "The source or draft has changed. Reopen the publishing draft.")
    snippet = {
        "title": values.title,
        "description": values.description,
        "tags": values.tags,
        "categoryId": "22",
    }
    status_object = {"privacyStatus": values.visibility, "selfDeclaredMadeForKids": values.made_for_kids}
    code, headers, body = transport(
        "POST",
        "https://www.googleapis.com/upload/youtube/v3/videos?"
        + urlencode({"uploadType": "resumable", "part": "snippet,status", "notifySubscribers": "false"}),
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
            "X-Upload-Content-Type": values.mime,
            "X-Upload-Content-Length": str(values.size),
        },
        body=json.dumps({"snippet": snippet, "status": status_object}).encode(),
    )
    checked((code, headers, body))
    location = headers.get("location", "")
    parsed = urlsplit(location)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"www.googleapis.com", "youtube.googleapis.com"}
        or parsed.port not in {None, 443}
        or parsed.username
    ):
        raise HTTPException(502, "YouTube returned an invalid upload session.")
    cipher = config()[3]
    record = {
        **query,
        "request_id": str(values.request_id),
        "size": values.size,
        "mime": values.mime,
        "title": values.title,
        "offset": 0,
        "fingerprint": fingerprint,
        "session": cipher.encrypt(location.encode()).decode(),
        "expires_at": datetime.now(UTC) + timedelta(days=6),
    }
    db.youtube_uploads.create_index("expires_at", expireAfterSeconds=0)
    # Parallel initializations may create empty provider sessions; only the one persisted
    # here is ever handed bytes. Retrying a request ID cannot create a second video.
    stored = db.youtube_uploads.find_one_and_update(
        {"_id": query["_id"]},
        {"$setOnInsert": record},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    if stored["generation"] != link["generation"] or stored["fingerprint"] != fingerprint:
        raise HTTPException(409, "That upload belongs to another connection or has different settings.")
    return transfer_view(stored)


@router.get("/uploads/{upload_id}")
def upload_status(upload_id: UUID, user=Depends(current_user), db=Depends(get_db)):
    link, _ = connected(db, user["_id"])
    doc = db.youtube_uploads.find_one(transfer_query(user["_id"], link, upload_id))
    if not doc:
        raise HTTPException(404, "Upload session not found.")
    return transfer_view(doc)


def received_bytes(headers, size):
    value = headers.get("range")
    if not value:
        return 0
    match = re.fullmatch(r"bytes=0-(\d+)", value)
    if not match or int(match[1]) >= size:
        raise HTTPException(502, "YouTube returned an invalid upload position.")
    return int(match[1]) + 1


def transfer_chunk(upload_id, data, bounds, user, db):
    start, end, size = bounds
    link, token = connected(db, user["_id"])
    query = transfer_query(user["_id"], link, upload_id)
    doc = db.youtube_uploads.find_one(query)
    if not doc:
        raise HTTPException(404, "Upload session not found.")
    if doc.get("video_id"):
        return transfer_view(doc)
    if (
        size != doc["size"]
        or start > end
        or end >= size
        or len(data) != end - start + 1
        or (end + 1 != size and len(data) % (256 * 1024) != 0)
    ):
        raise HTTPException(422, "Invalid upload chunk length or range.")
    nonce = secrets.token_hex(16)
    locked = db.youtube_uploads.find_one_and_update(
        {
            **query,
            "video_id": {"$exists": False},
            "$or": [{"busy_until": {"$exists": False}}, {"busy_until": {"$lt": datetime.now(UTC)}}],
        },
        {"$set": {"busy_until": datetime.now(UTC) + timedelta(minutes=2), "lock": nonce}},
    )
    if not locked:
        raise HTTPException(409, "A chunk is already in progress. Resume in a moment.")
    lease = {**query, "lock": nonce}
    try:
        if locked["offset"] != start:
            return transfer_view(locked)
        url = decrypt(config()[3], locked["session"])
        headers = {"Authorization": "Bearer " + token, "Content-Type": doc["mime"]}
        if locked.get("uncertain"):
            outcome = transport(
                "PUT", url, headers={**headers, "Content-Range": f"bytes */{size}"}, body=b""
            )
            if outcome[0] == 308 and received_bytes(outcome[1], size) == start:
                outcome = None
        else:
            outcome = None
        if outcome is None:
            # Persist uncertainty before sending. A new process can query YouTube's offset
            # after a timeout or crash, including an already-accepted final chunk.
            db.youtube_uploads.update_one(lease, {"$set": {"uncertain": True}})
            outcome = transport(
                "PUT",
                url,
                headers={**headers, "Content-Range": f"bytes {start}-{end}/{size}"},
                body=data,
            )
        code, response_headers, result = outcome
        if code == 308:
            fields = {"offset": received_bytes(response_headers, size), "uncertain": False}
        else:
            checked(outcome)
            if code not in {200, 201} or not result.get("id"):
                raise HTTPException(502, "YouTube did not confirm the video upload.")
            fields = {
                "offset": size,
                "uncertain": False,
                "video_id": result["id"],
                "actual_visibility": result.get("status", {}).get("privacyStatus"),
            }
        update = {"$set": fields}
        if fields.get("video_id"):
            update["$unset"] = {"session": ""}
        saved = db.youtube_uploads.find_one_and_update(
            lease, update, return_document=ReturnDocument.AFTER
        )
        if not saved:
            raise HTTPException(409, "The upload connection changed. Check your YouTube channel.")
        if saved.get("video_id"):
            notify(
                db,
                user["_id"],
                "youtube:upload:" + saved["video_id"],
                "YouTube upload complete",
                saved["title"],
            )
        return transfer_view(saved)
    finally:
        db.youtube_uploads.update_one(lease, {"$unset": {"lock": "", "busy_until": ""}})


@router.put("/uploads/{upload_id}")
async def upload_chunk(
    upload_id: UUID, request: Request, user=Depends(current_user), db=Depends(get_db)
):
    header = request.headers.get("content-range", "")
    match = re.fullmatch(r"bytes (\d{1,10})-(\d{1,10})/(\d{1,10})", header)
    if not match:
        raise HTTPException(422, "The upload requires a byte range.")
    data = bytearray()
    async for block in request.stream():
        if len(data) + len(block) > CHUNK:
            raise HTTPException(413, "Use chunks of at most four MiB.")
        data.extend(block)
    return await run_in_threadpool(
        transfer_chunk, upload_id, bytes(data), tuple(map(int, match.groups())), user, db
    )
