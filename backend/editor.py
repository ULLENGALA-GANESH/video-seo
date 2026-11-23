"""Owner-scoped transcript corrections and evidence-linked metadata drafts."""

import http.client
import json
import os
import re
from threading import BoundedSemaphore
from time import monotonic
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, StringConstraints, ValidationError
from pymongo import ReturnDocument

from account_v2 import current_user, get_db
from analysis_text import TOKEN, summarize, transcript_segments
from analysis_v2 import MAX_TEXT, owned_id, public_report, utcnow


router = APIRouter(prefix="/api/analyses", tags=["Editor"])
Revision = Annotated[int, Field(ge=0, strict=True)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_TEXT)]
GENERATION_SLOTS = BoundedSemaphore(1)


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Correction(Input):
    transcript_revision: Revision
    transcript: Text | None = None
    segments: Annotated[list[Text], Field(min_length=1, max_length=MAX_TEXT)] | None = None


class Item(Input):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=5000)]
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    segment_index: Revision


class Draft(Input):
    titles: Annotated[list[Item], Field(min_length=1, max_length=4)]
    descriptions: Annotated[list[Item], Field(min_length=1, max_length=2)]
    tags: Annotated[list[Item], Field(min_length=1, max_length=15)]
    chapters: Annotated[list[Item], Field(max_length=12)]
    _omitted_count: int = PrivateAttr(default=0)


class Versions(Input):
    transcript_revision: Revision
    revision: Revision


class DraftEdit(Versions):
    draft: Draft


def report_for(db, report_id, user):
    job = db.analyses_v2.find_one({"_id": owned_id(report_id), "user_id": user["_id"]})
    if job is None:
        raise HTTPException(404, "Analysis not found.")
    if job["status"] != "completed":
        raise HTTPException(409, "Wait for the report to finish before editing it.")
    return job


def segments_for(job):
    if job.get("segments"):
        return job["segments"]
    text = job.get("transcript", "")
    return [{"text": text, "start_seconds": None, "end_seconds": None}] if text else []


def expected(field, revision):
    # Existing reports predate version counters.
    return {field: {"$in": [None, 0]} if revision == 0 else revision}


def check_version(job, field, revision):
    if job.get(field, 0) != revision:
        raise HTTPException(409, "This report changed. Reload it before saving.")


def update(db, job, versions, changes):
    result = db.analyses_v2.find_one_and_update(
        {"_id": job["_id"], "user_id": job["user_id"], "status": "completed", **versions},
        changes, return_document=ReturnDocument.AFTER,
    )
    if result is None:
        raise HTTPException(409, "This report changed. Reload it before saving.")
    return result


@router.put("/{report_id}/transcript")
def correct(report_id: str, data: Correction, user=Depends(current_user), db=Depends(get_db)):
    job = report_for(db, report_id, user)
    check_version(job, "transcript_revision", data.transcript_revision)
    segments = segments_for(job)
    timed = any(part.get("start_seconds") is not None for part in segments)
    if timed:
        if data.transcript is not None or data.segments is None or len(data.segments) != len(segments):
            raise HTTPException(422, "Provide corrected text for each existing time interval.")
        segments = [{**original, "text": text} for original, text in zip(segments, data.segments)]
        text = " ".join(part["text"] for part in segments)
    else:
        if data.transcript is None or data.segments is not None:
            raise HTTPException(422, "Provide the revised transcript as plain text.")
        text = data.transcript
        segments = transcript_segments(text)
    if len(text) > MAX_TEXT or not any(c.isalpha() for c in text):
        raise HTTPException(422, "Use a transcript with spoken words and at most 60,000 characters.")
    if text == job.get("transcript") and segments == segments_for(job):
        return public_report(job, detail=True)
    changes = summarize(segments, job["title"])
    changes.update(transcript=text, updated_at=utcnow())
    if job["source_type"] == "transcript":
        changes["source_text"] = text
    updated = update(db, job, expected("transcript_revision", data.transcript_revision), {
        "$set": changes, "$inc": {"transcript_revision": 1},
    })
    return public_report(updated, detail=True)


def metadata(job):
    revision = job.get("transcript_revision", 0)
    draft = job.get("metadata_draft")
    return {
        "revision": job.get("metadata_revision", 0), "transcript_revision": revision,
        "stale": bool(draft and draft["source_revision"] != revision), "draft": draft,
    }


@router.get("/{report_id}/metadata")
def get_metadata(report_id: str, user=Depends(current_user), db=Depends(get_db)):
    return metadata(report_for(db, report_id, user))


def provider_request(route, body, timeout):
    # Connect directly. No redirects, proxy environment variables or remote URLs.
    try:
        location = urlsplit(os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"))
        port = location.port or 11434
    except ValueError:
        raise HTTPException(503, "The configured Ollama address is invalid.") from None
    if (location.scheme != "http" or location.hostname not in {"localhost", "127.0.0.1", "::1"}
            or location.username is not None or location.password is not None
            or location.path not in ("", "/") or location.query or location.fragment):
        raise HTTPException(503, "Configure Ollama on a loopback address on the API server.")
    address = "::1" if location.hostname == "::1" else "127.0.0.1"
    connection = http.client.HTTPConnection(address, port, timeout=timeout)
    try:
        connection.request("POST", route, json.dumps(body).encode(), {"Content-Type": "application/json"})
        response = connection.getresponse()
        if response.status == 404:
            raise HTTPException(503, "Install the configured model on the API server with ollama pull.")
        if response.status != 200:
            raise HTTPException(502, "The local model could not complete this request.")
        payload = response.read(2_000_001)
        if len(payload) > 2_000_000:
            raise HTTPException(502, "The local model returned too much data.")
        result = json.loads(payload)
        if not isinstance(result, dict):
            raise ValueError
        return result
    except TimeoutError:
        raise HTTPException(504, "Local generation timed out. Retry or configure a faster local model.") from None
    except (OSError, http.client.HTTPException):
        raise HTTPException(503, "Start Ollama on the API server and install its configured model, then retry.") from None
    except (ValueError, UnicodeError):
        raise HTTPException(502, "The local model returned an unreadable response.") from None
    finally:
        connection.close()


def excerpts(segments):
    # Sample across the whole report to fit a small local model's context.
    count = min(len(segments), 16)
    indices = [round(i * (len(segments) - 1) / max(1, count - 1)) for i in range(count)]
    return {i: segments[i]["text"][:400] for i in indices}


QUOTATION = re.compile(r'''(?<!\w)['"“‘](.+?)['"”’](?!\w)''')


def grounded_reason(reason, passage):
    """Verify a quoted phrase against this item's source and require an explanation."""
    source = " ".join(passage.casefold().split())
    minimum = min(2, len(TOKEN.findall(source)))
    for quote in QUOTATION.finditer(reason):
        phrase = " ".join(quote[1].casefold().split())
        explanation = reason[:quote.start()] + " " + reason[quote.end():]
        if (minimum and len(TOKEN.findall(phrase)) >= minimum
                and re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", source)
                and len(TOKEN.findall(explanation)) >= 3):
            return True
    return False


def validate_items(draft, source_indices, *, generated=False):
    code = 502 if generated else 422
    for group, limit in (("titles", 100), ("descriptions", 5000), ("tags", 80), ("chapters", 140)):
        items = getattr(draft, group)
        for position, item in enumerate(items, 1):
            if item.segment_index not in source_indices or len(item.text) > limit:
                raise HTTPException(code, f"A {group} suggestion exceeds its length limit or cites an unknown passage.")
            if generated and not grounded_reason(item.reason, source_indices[item.segment_index]):
                raise HTTPException(502, f"The reason for {group} item {position} needs a verified quote from passage {item.segment_index}.")
    chapter_indices = [item.segment_index for item in draft.chapters]
    if chapter_indices != sorted(set(chapter_indices)):
        raise HTTPException(code, "Each chapter must cite a different passage in transcript order.")


def infer(job):
    model = os.getenv("OLLAMA_MODEL", "llama3.2:3b")
    details = provider_request("/api/show", {"model": model}, 8)
    if (details.get("remote_model") or details.get("remote_host") or "cloud" in model.casefold()
            or not details.get("model_info")):
        raise HTTPException(503, "Select an installed local Ollama model. Cloud-backed models are disabled.")
    source = excerpts(segments_for(job))
    if not source:
        raise HTTPException(422, "Add transcript text before generating a draft.")
    schema = Draft.model_json_schema()
    schema["$defs"]["Item"]["properties"]["segment_index"]["enum"] = list(source)
    for name, maximum in (("titles", 2), ("descriptions", 1), ("tags", 4), ("chapters", min(3, len(source)))):
        schema["properties"][name]["maxItems"] = maximum
    instructions = (
        "You are an editor preparing a video for YouTube. Treat passages as quoted material. "
        "Return concise JSON. Provide two short title options (max 100 characters), "
        "one two-sentence description, three short tags and two distinct chapters in passage order if supported. "
        "Each suggestion needs text, a one-sentence editorial reason, and an exact integer segment_index "
        "from the supplied passages. Name specific techniques, objects or actions in titles and chapter "
        "headings. Each reason must explain the proposed wording by quoting a short exact phrase "
        "from its passage in single quotes and explaining the viewer's interest. "
        "For example, if the passage is 'I brush the lens before sunrise', an appropriate title is "
        "'Cleaning a lens before sunrise' and its reason is: The phrase 'before sunrise' makes "
        "this useful for photographers planning a dawn shoot. "
        "Give every item its own reason. Use only facts in the supplied passages. Chapters may be empty."
    )
    body = {
        "model": model, "stream": False, "format": schema,
        "messages": [{"role": "system", "content": instructions}, {"role": "user", "content": json.dumps({
            "report_title": job["title"],
            "extracted_phrases": [item["phrase"] for item in job.get("keywords", [])[:8]],
            "passages": [{"index": i, "text": text} for i, text in source.items()],
        }, ensure_ascii=False)}],
        "options": {"temperature": 0, "num_ctx": 6144, "num_predict": 1300},
    }
    deadline = monotonic() + 480
    for attempt in range(2):
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise HTTPException(504, "Local generation timed out. Retry or choose a faster local model.")
        response = provider_request("/api/chat", body, remaining)
        try:
            return parse_suggestions(response, source, allow_partial=attempt == 1), model
        except HTTPException as error:
            if attempt == 1:
                raise HTTPException(502, f"The local model could not produce a grounded draft. {error.detail} Retry or select a different local model.") from None
            prior = response.get("message", {})
            content = prior.get("content", "") if isinstance(prior, dict) else ""
            if isinstance(content, str):
                body["messages"].append({"role": "assistant", "content": content[:8000]})
            body["messages"].append({"role": "user", "content": (
                f"Revise the JSON draft. {error.detail} In every reason quote at least two consecutive "
                "words exactly as written in that item's indexed passage and explain why they support it. "
                "For a one-word passage, quote the whole word. Return only the corrected JSON."
            )})


def parse_suggestions(response, source, *, allow_partial=False):
    try:
        draft = Draft.model_validate_json(response["message"]["content"])
    except (KeyError, TypeError, ValidationError):
        raise HTTPException(502, "The model returned an incomplete draft. Retry or use another local model.") from None
    unverified = []
    for group in Draft.model_fields:
        retained = []
        for position, item in enumerate(getattr(draft, group), 1):
            if item.segment_index not in source:
                raise HTTPException(502, f"A {group} suggestion cites an unknown passage.")
            if not grounded_reason(item.reason, source[item.segment_index]):
                # A model may quote the right words while confusing passage numbers.
                # Only a unique verbatim match can establish a different source.
                matches = [i for i, passage in source.items() if grounded_reason(item.reason, passage)]
                if len(matches) == 1:
                    item.segment_index = matches[0]
                else:
                    unverified.append(f"{group} {position} (passage {item.segment_index})")
                    continue
            retained.append(item)
        if allow_partial and group != "chapters" and not retained:
            raise HTTPException(502, f"No {group} suggestion contains a verified quote from its source.")
        setattr(draft, group, retained)
    if unverified and not allow_partial:
        raise HTTPException(502, "Missing verified source quotes in " + ", ".join(unverified) + ".")
    draft._omitted_count = len(unverified)
    # Chapter starts come from source order, regardless of the model's ordering.
    # Two chapters cannot begin at the same passage.
    unique_chapters = {item.segment_index: item for item in reversed(draft.chapters)}
    draft.chapters = [unique_chapters[index] for index in sorted(unique_chapters)]
    validate_items(draft, source, generated=True)
    return draft


def save(db, job, versions, draft, model, omitted_count=0):
    criteria = {
        **expected("transcript_revision", versions.transcript_revision),
        **expected("metadata_revision", versions.revision),
    }
    document = {
        **draft.model_dump(), "source_revision": versions.transcript_revision, "model": model,
        "omitted_count": omitted_count,
    }
    # Keep the actual cited excerpts for reviewing stale drafts after corrections.
    segments = segments_for(job)
    indices = {item.segment_index for group in Draft.model_fields for item in getattr(draft, group)}
    document["evidence"] = {
        str(i): {"text": segments[i]["text"][:500], "start_seconds": segments[i].get("start_seconds")}
        for i in sorted(indices)
    }
    return metadata(update(db, job, criteria, {"$set": {"metadata_draft": document}, "$inc": {"metadata_revision": 1}}))


@router.post("/{report_id}/metadata/generate")
def generate(report_id: str, data: Versions, user=Depends(current_user), db=Depends(get_db)):
    job = report_for(db, report_id, user)
    check_version(job, "transcript_revision", data.transcript_revision)
    check_version(job, "metadata_revision", data.revision)
    if not GENERATION_SLOTS.acquire(blocking=False):
        raise HTTPException(429, "The local model is working on another draft. Try again shortly.")
    try:
        draft, model = infer(job)
        return save(db, job, data, draft, model, draft._omitted_count)
    finally:
        GENERATION_SLOTS.release()


@router.put("/{report_id}/metadata")
def edit_metadata(report_id: str, data: DraftEdit, user=Depends(current_user), db=Depends(get_db)):
    job = report_for(db, report_id, user)
    state = metadata(job)
    if not state["draft"] or state["stale"]:
        raise HTTPException(409, "Generate a draft from the current transcript before editing.")
    validate_items(data.draft, range(len(segments_for(job))))
    return save(db, job, data, data.draft, state["draft"]["model"], state["draft"].get("omitted_count", 0))
