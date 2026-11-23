"""On-demand YouTube search snapshots and private editorial competition assessments."""
import hashlib
import json
import os
import re
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request as HttpRequest, urlopen
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from pymongo.errors import DuplicateKeyError

from account_v2 import current_user, get_db
from analysis_v2 import owned_id

router = APIRouter(prefix="/api/research", tags=["Keyword research"])
Region = Literal["US", "IN", "GB", "CA", "AU", "DE"]
CACHE_AGE = timedelta(hours=6)
BASE_URL = "https://www.googleapis.com/youtube/v3/"
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}\Z")


class Lookup(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    report_id: str = Field(min_length=1, max_length=24)
    phrase: str = Field(min_length=1, max_length=200)
    region: Region = "US"


class Assessment(Lookup):
    level: Literal["unrated", "low", "moderate", "high"]
    notes: str = Field(max_length=1000)


@lru_cache(maxsize=2)
def ensure_indexes(db):
    db.research_cache.create_index("expires_at", expireAfterSeconds=0)
    db.research_limits.create_index("expires_at", expireAfterSeconds=0)


def digest(*parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()


def private_phrase(db, owner, query):
    report = db.analyses_v2.find_one(
        {"_id": owned_id(query.report_id), "user_id": owner, "status": "completed"},
        {"keywords.phrase": 1},
    )
    if report is None:
        raise HTTPException(404, "Completed report not found.")
    return matching_phrase(report["keywords"], query.phrase)


def matching_phrase(keywords, phrase):
    for item in keywords:
        if item["phrase"].casefold() == phrase.casefold():
            return item["phrase"]
    raise HTTPException(422, "Choose a keyword from this report.")


@lru_cache(maxsize=1)
def sample_keywords():
    frontend = Path(__file__).resolve().parents[1] / "frontend"
    for folder in (Path(os.getenv("FRONTEND_DIST") or frontend / "dist"), frontend / "public"):
        file = folder / "sample-report.json"
        if file.is_file():
            return json.loads(file.read_text(encoding="utf-8-sig"))["keywords"]
    raise HTTPException(503, "Sample keyword research is unavailable.")


def google(endpoint, params, key):
    # Google's system-parameter header keeps credentials out of URLs and access logs.
    address = BASE_URL + endpoint + "?" + urlencode(params)
    req = HttpRequest(address, headers={"Accept": "application/json"})
    req.add_unredirected_header("X-Goog-Api-Key", key)
    try:
        with urlopen(req, timeout=8) as response:
            body = json.loads(response.read(512_001))
        if not isinstance(body, dict) or not isinstance(body.get("items"), list):
            raise ValueError("Unexpected provider response")
        return body["items"]
    except HTTPError as error:
        try:
            reasons = {item.get("reason") for item in json.loads(error.read(16_384))["error"]["errors"]}
        except (ValueError, KeyError, TypeError, AttributeError):
            reasons = set()
        if error.code == 429 or reasons.intersection({"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded", "userRateLimitExceeded"}):
            raise HTTPException(429, "YouTube's lookup quota is currently exhausted. Try again later.") from None
        if error.code in (400, 401, 403):
            raise HTTPException(503, "YouTube rejected this lookup. Check the server API key, API access, and restrictions.") from None
        raise HTTPException(502, "YouTube could not complete this lookup. Try again later.") from None
    except (URLError, TimeoutError, OSError, ValueError):
        raise HTTPException(502, "YouTube could not be reached or returned an unreadable response.") from None


def public_count(statistics, field):
    value = statistics.get(field)
    return int(value) if str(value).isascii() and str(value).isdigit() else None


def fetch_youtube(phrase, region, key):
    search = google("search", {
        "part": "snippet", "type": "video", "order": "relevance", "maxResults": 10,
        "q": phrase, "regionCode": region, "fields": "items(id/videoId)",
    }, key)
    ids = list(dict.fromkeys(item["id"]["videoId"] for item in search
                            if VIDEO_ID.fullmatch(item.get("id", {}).get("videoId", ""))))
    videos = []
    if ids:
        details = google("videos", {
            "part": "snippet,statistics", "id": ",".join(ids),
            "fields": "items(id,snippet(title,channelTitle,publishedAt),statistics(viewCount,likeCount,commentCount))",
        }, key)
        by_id = {item["id"]: item for item in details if item.get("id") in ids}
        for video_id in ids:
            item = by_id.get(video_id)
            if not item:
                continue  # A result can be removed between the search and details requests.
            snippet, stats = item.get("snippet", {}), item.get("statistics", {})
            videos.append({
                "video_id": video_id, "title": snippet.get("title", ""), "channel": snippet.get("channelTitle", ""),
                "published_at": snippet.get("publishedAt"), "url": "https://www.youtube.com/watch?v=" + video_id,
                "views": public_count(stats, "viewCount"), "likes": public_count(stats, "likeCount"),
                "comments": public_count(stats, "commentCount"),
            })
    return {"phrase": phrase, "region": region, "collected_at": datetime.now(UTC).isoformat(), "videos": videos}


def consume(db, name, seconds, limit, message, now):
    bucket = int(now.timestamp()) // seconds
    key = digest(name, bucket)
    try:
        db.research_limits.update_one(
            {"_id": key, "count": {"$lt": limit}},
            {"$inc": {"count": 1}, "$setOnInsert": {"expires_at": now + timedelta(seconds=seconds + 60)}},
            upsert=True,
        )
    except DuplicateKeyError:
        raise HTTPException(429, message, headers={"Retry-After": str((bucket + 1) * seconds - int(now.timestamp()))}) from None


def daily_limit():
    try:
        return max(0, int(os.getenv("YOUTUBE_RESEARCH_DAILY_LIMIT", "60")))
    except ValueError:
        raise HTTPException(503, "The server's research quota setting is invalid.") from None


def lookup(db, scope, actor, phrase, region, *, guest=False):
    now = datetime.now(UTC)
    ensure_indexes(db)
    cache_key = digest(scope, phrase.casefold(), region)
    cache = db.research_cache
    existing = cache.find_one({"_id": cache_key, "expires_at": {"$gt": now}, "result": {"$exists": True}})
    if existing:
        return {**existing["result"], "cached": True}
    api_key = os.getenv("YOUTUBE_API_KEY")
    if not api_key:
        raise HTTPException(503, "Live research is unavailable. Configure YOUTUBE_API_KEY on the server.")
    limit = daily_limit()
    if not limit:
        raise HTTPException(503, "Live research is disabled by this server's daily budget.")
    lease = uuid4().hex
    try:
        cache.update_one(
            {"_id": cache_key, "expires_at": {"$lte": now}},
            {"$set": {"expires_at": now + timedelta(minutes=1), "lease": lease}, "$unset": {"result": ""}},
            upsert=True,
        )
    except DuplicateKeyError:
        # A concurrent caller may have finished just after the initial cache read.
        existing = cache.find_one({"_id": cache_key, "result": {"$exists": True}, "expires_at": {"$gt": now}})
        if existing:
            return {**existing["result"], "cached": True}
        raise HTTPException(409, "This lookup is already in progress. Try again shortly.") from None
    own_lease = {"_id": cache_key, "lease": lease}
    try:
        consume(db, actor, 3600, 3 if guest else 6, "Hourly research limit reached. Try again later.", now)
        consume(db, "project", 86400, limit, "Today's research budget has been used. Try again tomorrow (UTC).", now)
        result = fetch_youtube(phrase, region, api_key)
        cache.update_one(own_lease, {"$set": {"result": result, "expires_at": now + CACHE_AGE}, "$unset": {"lease": ""}})
        return {**result, "cached": False}
    except Exception:
        cache.delete_one(own_lease)
        raise


@router.post("")
def private_lookup(query: Lookup, user=Depends(current_user), db=Depends(get_db)):
    phrase = private_phrase(db, user["_id"], query)
    actor = "user:" + str(user["_id"])
    return lookup(db, actor, actor, phrase, query.region)


@router.post("/sample")
def sample_lookup(query: Lookup, request: Request, db=Depends(get_db)):
    if query.report_id != "sample":
        raise HTTPException(404, "Sample report not found.")
    phrase = matching_phrase(sample_keywords(), query.phrase)
    actor = "guest:" + (request.client.host if request.client else "unknown")
    return lookup(db, "sample", actor, phrase, query.region, guest=True)


def assessment_key(owner, query, phrase):
    return digest(str(owner), query.report_id, phrase.casefold(), query.region)


@router.post("/assessment/read")
def read_assessment(query: Lookup, user=Depends(current_user), db=Depends(get_db)):
    phrase = private_phrase(db, user["_id"], query)
    saved = db.research_assessments.find_one({"_id": assessment_key(user["_id"], query, phrase)}, {"_id": 0})
    return saved or {"level": "unrated", "notes": "", "updated_at": None}


@router.put("/assessment")
def save_assessment(query: Assessment, user=Depends(current_user), db=Depends(get_db)):
    phrase = private_phrase(db, user["_id"], query)
    key = assessment_key(user["_id"], query, phrase)
    if query.level == "unrated" and not query.notes:
        db.research_assessments.delete_one({"_id": key})
        return {"level": "unrated", "notes": "", "updated_at": None}
    saved = {"level": query.level, "notes": query.notes, "updated_at": datetime.now(UTC).isoformat()}
    db.research_assessments.update_one({"_id": key}, {"$set": saved}, upsert=True)
    return saved
