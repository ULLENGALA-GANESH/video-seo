"""Serve the API and production web application from one origin."""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pymongo.errors import PyMongoError
from starlette.concurrency import run_in_threadpool

import channel
import research
import notifications
import editor
import account_v2
import analysis_v2


class ProtectOAuthCallback(logging.Filter):
    """Keep single-use Google authorization codes out of Uvicorn's access log."""

    def filter(self, record):
        args = record.args
        if isinstance(args, tuple) and len(args) == 5:
            client, method, path, version, status = args
            if isinstance(path, str) and path.startswith("/api/channel/oauth/callback?"):
                record.args = (client, method, path.partition("?")[0], version, status)
        return True


@asynccontextmanager
async def lifespan(app: FastAPI):
    access_log = logging.getLogger("uvicorn.access")
    callback_filter = ProtectOAuthCallback()
    access_log.addFilter(callback_filter)
    worker = None
    if os.getenv("INLINE_WORKER", "true").lower() in {"1", "true", "yes"}:
        worker = analysis_v2.start_worker()
    try:
        yield
    finally:
        access_log.removeFilter(callback_filter)
        if worker:
            thread, stop_event = worker
            stop_event.set()
            thread.join(timeout=5)
        account_v2.close_database()


app = FastAPI(
    title="AI Video SEO Analysis System", version="3.0.0", lifespan=lifespan,
    docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json",
)
app.include_router(account_v2.router)
app.include_router(analysis_v2.router)
app.include_router(channel.router)
app.include_router(research.router)
app.include_router(editor.router)
app.include_router(notifications.router)


def allowed_write(request):
    # Browsers control Origin and Fetch Metadata. API clients without browser
    # headers may use JSON or explicitly identify a scripted multipart request.
    site = request.headers.get("sec-fetch-site")
    if site == "cross-site":
        return False
    origin = request.headers.get("origin")
    if origin is not None:
        expected = os.getenv("APP_ORIGIN") or str(request.base_url)
        return origin == expected.rstrip("/")
    if site:
        return site == "same-origin"
    return (
        request.headers.get("content-type", "").split(";", 1)[0].strip() == "application/json"
        or request.headers.get("x-requested-with") == "XMLHttpRequest"
    )


@app.middleware("http")
async def browser_security(request: Request, call_next):
    api_path = request.url.path == "/api" or request.url.path.startswith("/api/")
    if api_path and request.method not in {"GET", "HEAD", "OPTIONS"} and not allowed_write(request):
        return JSONResponse({"detail": "This request must come from the app."}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    if api_path:
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    # Pydantic includes original inputs in its errors. Passwords and
    # transcripts should never be echoed in responses.
    messages = []
    for error in exc.errors():
        field = str(error["loc"][-1]) if error["loc"] else "request"
        messages.append(f"{field}: {error['msg']}")
    return JSONResponse({"detail": ". ".join(messages)}, status_code=422)


@app.exception_handler(PyMongoError)
async def database_unavailable(request, exc):
    return JSONResponse({"detail": "The workspace database is temporarily unavailable."}, status_code=503)


@app.get("/api/health", tags=["Status"])
def health():
    return {"status": "ok"}


@app.get("/api/ready", tags=["Status"])
def ready(db=Depends(account_v2.get_db)):
    db.command("ping")
    account_v2.ensure_indexes(db)
    analysis_v2.ensure_indexes(db)
    return {"status": "ready"}


def frontend_directory():
    default = Path(__file__).resolve().parents[1] / "frontend" / "dist"
    return Path(os.getenv("FRONTEND_DIST", default)).resolve()


def frontend(path):
    directory = frontend_directory()
    target = (directory / path).resolve()
    if not target.is_relative_to(directory):
        return JSONResponse({"detail": "Not found."}, status_code=404)
    if target.is_file():
        return FileResponse(target, headers={"Cache-Control": "no-cache"} if target.name == "index.html" else None)
    if path.startswith("assets/") or target.suffix:
        return JSONResponse({"detail": "Asset not found."}, status_code=404)
    index = directory / "index.html"
    if not index.is_file():
        return JSONResponse({"detail": "Build the frontend or start its dev server."}, status_code=404)
    return FileResponse(index, headers={"Cache-Control": "no-cache"})


@app.exception_handler(404)
async def missing_route(request: Request, exc):
    path = request.url.path.lstrip("/")
    if path == "api" or path.startswith("api/") or request.method not in {"GET", "HEAD"}:
        return JSONResponse({"detail": exc.detail}, status_code=404, headers=exc.headers)
    return await run_in_threadpool(frontend, path)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="127.0.0.1", port=int(os.getenv("PORT", "8000")))
