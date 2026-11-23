"""Accounts and revocable browser sessions shared by the API and worker."""

import hashlib
import os
import re
import secrets
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from threading import BoundedSemaphore
from typing import Annotated

import bcrypt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, StringConstraints
from pymongo import MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError

load_dotenv(Path(__file__).with_name(".env"))

router = APIRouter(prefix="/api", tags=["Accounts"])
COOKIE_NAME = "framewise_session"
SESSION_LIFETIME = timedelta(days=7)
PASSWORD_HASHER = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)
PASSWORD_SLOTS = BoundedSemaphore(4)
DisplayName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: Annotated[EmailStr, Field(max_length=254)]
    password: Annotated[SecretStr, Field(min_length=1, max_length=1024)]


class Signup(Credentials):
    name: DisplayName
    password: Annotated[SecretStr, Field(min_length=8, max_length=128)]


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: DisplayName


class Account(BaseModel):
    id: str
    name: str
    email: str


@lru_cache(maxsize=1)
def mongo_client():
    return MongoClient(
        os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017"),
        connect=False, serverSelectionTimeoutMS=2500, tz_aware=True,
    )


def get_db():
    return mongo_client()[os.environ.get("DB_NAME", "video_seo_db")]


@lru_cache(maxsize=2)
def ensure_indexes(db):
    # A sparse key preserves old user records that predate normalized addresses.
    db.users.create_index("email_normalized", unique=True, sparse=True)
    db.users.create_index("email")
    db.web_sessions.create_index("expires_at", expireAfterSeconds=0)
    db.auth_attempts.create_index("expires_at", expireAfterSeconds=0)


def close_database():
    ensure_indexes.cache_clear()
    if mongo_client.cache_info().currsize:
        mongo_client().close()
        mongo_client.cache_clear()


def public_account(user):
    return Account(id=str(user["_id"]), name=user.get("name") or user.get("username") or "Creator", email=user["email"])


def find_by_email(db, email):
    # Preserve exact matches for old databases that allowed mixed-case emails.
    user = db.users.find_one({"email": email}) or db.users.find_one({"email_normalized": email.casefold()})
    if user:
        return user
    return db.users.find_one({
        "email_normalized": {"$exists": False},
        "email": {"$regex": "^" + re.escape(email) + "$", "$options": "i"},
    })


def limit_auth_requests(request, db):
    # A fixed five-minute bucket is shared by API replicas. Store no raw address.
    now = datetime.now(UTC)
    address = request.client.host if request.client else "unknown"
    bucket = int(now.timestamp()) // 300
    key = hashlib.sha256(f"{bucket}:{address}".encode()).hexdigest()
    entry = db.auth_attempts.find_one_and_update(
        {"_id": key},
        {"$inc": {"count": 1}, "$setOnInsert": {"expires_at": now + timedelta(minutes=6)}},
        upsert=True, return_document=ReturnDocument.AFTER,
    )
    if entry["count"] > 30:
        raise HTTPException(429, "Too many attempts. Try again in five minutes.", headers={"Retry-After": "300"})


@contextmanager
def password_slot():
    if not PASSWORD_SLOTS.acquire(blocking=False):
        raise HTTPException(429, "Sign-in is busy. Please try again shortly.", headers={"Retry-After": "2"})
    try:
        yield
    finally:
        PASSWORD_SLOTS.release()


def verify_password(stored, password):
    if not isinstance(stored, str):
        return False
    try:
        if stored.startswith(("$2a$", "$2b$", "$2y$")):
            # Passlib/bcrypt used only the first 72 bytes in the original app.
            return bcrypt.checkpw(password.encode()[:72], stored.encode())
        return PASSWORD_HASHER.verify(stored, password)
    except (InvalidHashError, VerificationError, ValueError):
        return False


@lru_cache(maxsize=1)
def dummy_hash():
    return PASSWORD_HASHER.hash(secrets.token_urlsafe(32))


def session_key(token):
    return hashlib.sha256(token.encode()).hexdigest()


def secure_cookie(request):
    return (
        request.url.scheme == "https"
        or os.getenv("APP_ORIGIN", "").lower().startswith("https://")
        or os.getenv("COOKIE_SECURE", "").lower() in {"1", "true", "yes"}
    )


def start_session(db, user, request, response):
    token = secrets.token_urlsafe(32)
    db.web_sessions.insert_one({
        "_id": session_key(token), "user_id": user["_id"],
        "expires_at": datetime.now(UTC) + SESSION_LIFETIME,
    })
    previous = request.cookies.get(COOKIE_NAME)
    if previous:
        db.web_sessions.delete_one({"_id": session_key(previous)})
    response.set_cookie(
        COOKIE_NAME, token, max_age=int(SESSION_LIFETIME.total_seconds()), path="/",
        httponly=True, secure=secure_cookie(request), samesite="lax",
    )
    return public_account(user)


def session_token(request: Request):
    token = request.cookies.get(COOKIE_NAME)
    if token and len(token) <= 128:
        return token
    raise HTTPException(401, "Please sign in to your workspace.")


def current_user(token=Depends(session_token), db=Depends(get_db)):
    session = db.web_sessions.find_one({"_id": session_key(token), "expires_at": {"$gt": datetime.now(UTC)}})
    if session:
        user = db.users.find_one({"_id": session["user_id"]}, {"name": 1, "username": 1, "email": 1})
        if user:
            return user
    raise HTTPException(401, "Please sign in to your workspace.")


@router.post("/auth/signup", response_model=Account, status_code=201)
def signup(payload: Signup, request: Request, response: Response, db=Depends(get_db)):
    ensure_indexes(db)
    limit_auth_requests(request, db)
    email = str(payload.email).casefold()
    if find_by_email(db, email):
        raise HTTPException(409, "An account already uses this email.")
    with password_slot():
        password_hash = PASSWORD_HASHER.hash(payload.password.get_secret_value())
    now = datetime.now(UTC)
    user = {
        "email": email, "email_normalized": email, "name": payload.name,
        "password_hash": password_hash, "created_at": now, "updated_at": now,
    }
    try:
        db.users.insert_one(user)
    except DuplicateKeyError:
        raise HTTPException(409, "An account already uses this email.") from None
    return start_session(db, user, request, response)


@router.post("/auth/login", response_model=Account)
def login(payload: Credentials, request: Request, response: Response, db=Depends(get_db)):
    ensure_indexes(db)
    limit_auth_requests(request, db)
    user = find_by_email(db, str(payload.email))
    password = payload.password.get_secret_value()
    with password_slot():
        field = next((key for key in ("password_hash", "password", "hashed_password") if user and user.get(key)), None)
        stored = user[field] if field else dummy_hash()
        if not verify_password(stored, password) or not field:
            raise HTTPException(401, "Incorrect email or password.")
        if field != "password_hash" or not stored.startswith("$argon2") or PASSWORD_HASHER.check_needs_rehash(stored):
            db.users.update_one(
                {"_id": user["_id"], field: stored},
                {"$set": {"password_hash": PASSWORD_HASHER.hash(password)}, "$unset": {"password": "", "hashed_password": ""}},
            )
    return start_session(db, user, request, response)


@router.get("/auth/me", response_model=Account)
def me(user=Depends(current_user)):
    return public_account(user)


@router.post("/auth/logout", status_code=204)
def logout(request: Request, response: Response, db=Depends(get_db)):
    token = request.cookies.get(COOKIE_NAME)
    if token:
        db.web_sessions.delete_one({"_id": session_key(token)})
    response.delete_cookie(COOKIE_NAME, path="/", secure=secure_cookie(request), httponly=True, samesite="lax")


@router.patch("/profile", response_model=Account)
def update_profile(payload: ProfileUpdate, db=Depends(get_db), user=Depends(current_user)):
    result = db.users.find_one_and_update(
        {"_id": user["_id"]},
        {"$set": {"name": payload.name, "updated_at": datetime.now(UTC)}},
        projection={"name": 1, "username": 1, "email": 1}, return_document=ReturnDocument.AFTER,
    )
    if result is None:
        raise HTTPException(401, "Your account is no longer available.")
    return public_account(result)
