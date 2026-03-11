import asyncio
import json
import time
from typing import Optional

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, field_validator

from infra.log import logger
from infra.telemetry.metrics import (
    chat_request_total,
    chat_request_duration,
    chat_stream_duration,
    chat_active_streams,
    _token_prefix,
)
import services.state as state
from services.bridge import CHAT_INBOX_PREFIX

router = APIRouter()

_SSE_TIMEOUT = 300  # 5 minutes
_SSE_BLOCK_MS = 1000  # XREADGROUP block timeout — short so we can send heartbeats
_HEARTBEAT_INTERVAL = 15.0  # seconds between SSE heartbeat comments


def _record_request(status: str, code: int, token_prefix: str, t0: float) -> None:
    """Record request counter + duration histogram for a given status."""
    attrs = {"status": status, "code": str(code), "token_prefix": token_prefix}
    chat_request_total.add(1, attrs)
    chat_request_duration.record(time.time() - t0, attrs)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class MediaItem(BaseModel):
    type: str            # "url" | "base64" | "s3_key" (only "url" implemented now)
    content: str         # primary value: URL / base64 data / S3 key
    mimeType: str = ""   # required only for type="base64"

    @field_validator("content")
    @classmethod
    def validate_content(cls, v):
        if not v or not v.strip():
            raise ValueError("Media item content must not be empty")
        return v


class ChatRequest(BaseModel):
    content: str = ""
    sessionId: Optional[str] = None
    media: Optional[list[MediaItem]] = None

    @field_validator("media")
    @classmethod
    def validate_media(cls, v):
        if v is not None and len(v) > 10:
            raise ValueError("Too many media items (max 10)")
        return v


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------

async def _authenticate(authorization: Optional[str]) -> Optional[str]:
    """Extract and validate token from Authorization: Bearer header.

    Returns the validated token string, or None if invalid.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        return None

    token = authorization[7:].strip()
    if not token:
        return None

    if await state.token_manager.validate(token):
        return token
    return None


# ---------------------------------------------------------------------------
# Session resolution
# ---------------------------------------------------------------------------

async def _resolve_session(
    token: str,
    session_id: Optional[str],
) -> tuple[str, int]:
    """Resolve or auto-create a session.

    - If session_id is provided, validate it exists.
    - If not provided, create a new session.

    Returns (session_id, session_number).
    Raises ValueError with a message on failure.
    """
    bridge = state.bridge

    if session_id:
        match = await bridge.get_session(token, session_id)
        if not match:
            raise ValueError(f"Session not found: {session_id}")
        return match[0], match[1]

    # Create new session
    return await bridge.create_session(token)


# ---------------------------------------------------------------------------
# SSE formatting
# ---------------------------------------------------------------------------

def _sse_event(event_type: str, data: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _sse_comment() -> str:
    return ": heartbeat\n\n"


# ---------------------------------------------------------------------------
# SSE stream generator
# ---------------------------------------------------------------------------

async def _stream_response(
    token: str,
    session_id: str,
    session_number: int,
    req_id: str,
):
    """Consume events from Redis Stream inbox and yield SSE events."""
    queue = state.queue
    inbox = f"{CHAT_INBOX_PREFIX}{token}:{session_id}"
    deadline = time.time() + _SSE_TIMEOUT
    last_heartbeat = time.time()

    # First event: session info
    yield _sse_event("session", {
        "sessionId": session_id,
        "sessionNumber": session_number,
    })

    try:
        while time.time() < deadline:
            result = await queue.consume(
                inbox, group="sse", consumer=req_id,
                block_ms=_SSE_BLOCK_MS,
            )

            if result is None:
                # No message — send heartbeat if interval elapsed
                now = time.time()
                if now - last_heartbeat >= _HEARTBEAT_INTERVAL:
                    yield _sse_comment()
                    last_heartbeat = now
                else:
                    await asyncio.sleep(0.1)
                continue

            msg_id, raw = result
            await queue.ack(inbox, "sse", msg_id)
            await queue.delete_message(inbox, msg_id)

            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("SSE: invalid JSON in inbox (token={}...)", token[:10])
                continue

            event_type = event.pop("type", "message")

            # For media events, the payload is nested under "data" key
            # so that SSE data contains only {type, content} without the event type
            if event_type == "media":
                event_data = event.pop("data", None)
                if not event_data:
                    logger.warning("SSE: media event missing data payload (token={}...)", token[:10])
                    continue
            else:
                event_data = event
            yield _sse_event(event_type, event_data)

            # Terminal events — close the stream
            if event_type in ("done", "error"):
                return

        # Timeout reached
        yield _sse_event("error", {"content": "Stream timeout"})
    except asyncio.CancelledError:
        # Client disconnected
        logger.info("SSE: client disconnected (token={}...)", token[:10])
    except Exception:
        logger.exception("SSE: stream error (token={}...)", token[:10])
        yield _sse_event("error", {"content": "Internal server error"})


async def _stream_with_cleanup(
    token: str,
    session_id: str,
    session_number: int,
    req_id: str,
):
    """Wrap _stream_response and delete the chat inbox stream when done."""
    tp = _token_prefix(token)
    stream_attrs = {"token_prefix": tp}

    # Track active stream
    chat_active_streams.add(1, stream_attrs)
    stream_start = time.time()
    close_reason = "done"

    try:
        async for event in _stream_response(token, session_id, session_number, req_id):
            yield event
            # Detect close reason from terminal events
            if event.startswith("event: error"):
                if "Stream timeout" in event:
                    close_reason = "timeout"
                else:
                    close_reason = "error"
    except asyncio.CancelledError:
        close_reason = "client_disconnect"
        raise
    except Exception:
        close_reason = "error"
        raise
    finally:
        chat_active_streams.add(-1, stream_attrs)
        stream_duration = time.time() - stream_start

        chat_stream_duration.record(
            stream_duration,
            {"close_reason": close_reason, "token_prefix": tp},
        )

        try:
            inbox = f"{CHAT_INBOX_PREFIX}{token}:{session_id}"
            await state.queue.delete_queue(inbox)
        except Exception:
            logger.warning("SSE: cleanup failed (token={}...)", token[:10])


# ---------------------------------------------------------------------------
# POST /bridge/chat — Dialogue endpoint (SSE stream response)
# ---------------------------------------------------------------------------

@router.post("/bridge/chat")
async def chat_sse(
    body: ChatRequest,
    authorization: Optional[str] = Header(default=None),
):
    t0 = time.time()

    # Authenticate
    token = await _authenticate(authorization)
    if not token:
        logger.warning("SSE: auth failed (token missing or invalid)")
        _record_request("auth_fail", 401, "", t0)
        return JSONResponse(
            status_code=401,
            content={"ok": False, "error": "Invalid or missing token"},
        )

    tp = _token_prefix(token)

    # Validate message content — normalize media URLs
    content = body.content or ""
    media_urls: list[str] = []

    if body.media:
        for item in body.media:
            if item.type == "url":
                if not item.content.startswith(("http://", "https://")):
                    logger.warning("SSE: bad request — invalid media URL scheme: {} (token={}...)", item.content, token[:10])
                    _record_request("bad_request", 400, tp, t0)
                    return JSONResponse(
                        status_code=400,
                        content={"ok": False, "error": f"Invalid media URL scheme: {item.content}"},
                    )
                media_urls.append(item.content)
            else:
                logger.warning("SSE: bad request — unsupported media type: {} (token={}...)", item.type, token[:10])
                _record_request("bad_request", 400, tp, t0)
                return JSONResponse(
                    status_code=400,
                    content={"ok": False, "error": f"Unsupported media type: {item.type}"},
                )

    if not content and not media_urls:
        logger.warning("SSE: bad request — empty message (token={}...)", token[:10])
        _record_request("bad_request", 400, tp, t0)
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": "Empty message"},
        )

    # Check bot connected
    if not await state.bridge.is_bot_connected(token):
        logger.warning("SSE: no bot connected (token={}...)", token[:10])
        _record_request("no_bot", 400, tp, t0)
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": "No bot connected"},
        )

    # Resolve session
    try:
        session_id, session_number = await _resolve_session(token, body.sessionId)
    except ValueError as e:
        logger.warning("SSE: session not found {} (token={}...)", body.sessionId, token[:10])
        _record_request("session_not_found", 404, tp, t0)
        return JSONResponse(
            status_code=404,
            content={"ok": False, "error": str(e)},
        )

    # Clear stale events and reset consumer group for this SSE request
    queue = state.queue
    inbox = f"{CHAT_INBOX_PREFIX}{token}:{session_id}"
    await queue.purge(inbox)
    await queue.ensure_group(inbox, "sse")

    # Send message to bot via Redis Stream inbox
    req_id = await state.bridge.send_to_bot(
        token, content,
        media_urls=media_urls or None,
        session_id=session_id,
    )
    if not req_id:
        logger.error("SSE: send_to_bot failed (token={}...)", token[:10])
        _record_request("send_fail", 500, tp, t0)
        return JSONResponse(
            status_code=500,
            content={"ok": False, "error": "Failed to send message to bot"},
        )

    # Success — entering SSE stream
    _record_request("success", 200, tp, t0)

    logger.info(
        "SSE: chat started req={} session={} (token={}...)",
        req_id, session_id[:8], token[:10],
    )

    return StreamingResponse(
        _stream_with_cleanup(token, session_id, session_number, req_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# GET /bridge/chat/sessions — List sessions
# ---------------------------------------------------------------------------

@router.get("/bridge/chat/sessions")
async def list_sessions(
    authorization: Optional[str] = Header(default=None),
):
    validated = await _authenticate(authorization)
    if not validated:
        logger.warning("SSE: sessions auth failed (list)")
        return JSONResponse(
            status_code=401,
            content={"ok": False, "error": "Invalid or missing token"},
        )

    sessions = await state.bridge.get_sessions(validated)

    return {
        "ok": True,
        "sessions": [{"id": s[0], "number": s[1]} for s in sessions],
    }


# ---------------------------------------------------------------------------
# POST /bridge/chat/sessions — Create new session
# ---------------------------------------------------------------------------

@router.post("/bridge/chat/sessions")
async def create_session(
    authorization: Optional[str] = Header(default=None),
):
    validated = await _authenticate(authorization)
    if not validated:
        logger.warning("SSE: sessions auth failed (create)")
        return JSONResponse(
            status_code=401,
            content={"ok": False, "error": "Invalid or missing token"},
        )

    session_id, session_number = await state.bridge.create_session(validated)
    sessions = await state.bridge.get_sessions(validated)

    return {
        "ok": True,
        "sessionId": session_id,
        "sessionNumber": session_number,
        "sessions": [{"id": s[0], "number": s[1]} for s in sessions],
    }
