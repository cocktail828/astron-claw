"""Centralised error codes and error response helper.

All HTTP/WebSocket/SSE error messages and status codes are defined here
as ``Err`` enum members so that router and service code never uses magic
values directly.

Usage::

    from infra.errors import Err, error_response

    # HTTP error
    return error_response(Err.AUTH_INVALID_TOKEN)

    # HTTP error with dynamic detail
    return error_response(Err.MEDIA_UNSUPPORTED_TYPE, detail=item.type)

    # WebSocket close
    await ws.close(code=Err.WS_INVALID_TOKEN.status, reason=Err.WS_INVALID_TOKEN.message)

    # SSE event
    yield _sse_event("error", {"content": Err.CHAT_STREAM_TIMEOUT.message})
"""

from enum import Enum

from fastapi.responses import JSONResponse


class Err(Enum):
    """Application error codes.

    Each member is a tuple of ``(http_status_code, error_message)``.
    For SSE-only or internal errors, *status* is ``None``.
    """

    # ── Auth (token) ──────────────────────────────────────
    AUTH_INVALID_TOKEN      = (401, "Invalid or missing token")
    AUTH_MISSING_AUTH       = (401, "Missing authorization")
    AUTH_INVALID_SESSION    = (401, "Invalid admin session")
    AUTH_UNAUTHORIZED       = (401, "Unauthorized")
    AUTH_WRONG_PASSWORD     = (401, "Wrong password")

    # ── Admin setup ───────────────────────────────────────
    ADMIN_PASSWORD_EXISTS   = (400, "Password already set")
    ADMIN_PASSWORD_SHORT    = (400, "Password too short")

    # ── Chat / SSE ────────────────────────────────────────
    CHAT_EMPTY_MESSAGE      = (400, "Empty message")
    CHAT_NO_BOT             = (400, "No bot connected")
    CHAT_SEND_FAILED        = (500, "Failed to send message to bot")
    CHAT_STREAM_TIMEOUT     = (None, "Stream timeout")
    CHAT_INTERNAL_ERROR     = (None, "Internal server error")

    # ── Media ─────────────────────────────────────────────
    MEDIA_FILE_TOO_LARGE    = (413, "File too large")
    MEDIA_INVALID_FILE      = (400, "Invalid file or unsupported type")
    MEDIA_BAD_URL_SCHEME    = (400, "Invalid media URL scheme")
    MEDIA_UNSUPPORTED_TYPE  = (400, "Unsupported media type")

    # ── Session ───────────────────────────────────────────
    SESSION_NOT_FOUND       = (404, "Session not found")

    # ── Token (admin CRUD) ────────────────────────────────
    TOKEN_NOT_FOUND         = (404, "Token not found")

    # ── WebSocket ─────────────────────────────────────────
    WS_INVALID_TOKEN        = (4001, "Invalid or missing bot token")
    WS_DUPLICATE_BOT        = (4002, "Bot already connected")

    # ── Bot (internal) ────────────────────────────────────
    BOT_UNKNOWN_ERROR       = (None, "Unknown error from bot")

    def __init__(self, status: int | None, message: str):
        self.status = status
        self.message = message

    @property
    def code(self) -> int:
        """Return the HTTP status code as the integer error code."""
        return self.status or 0


def error_response(err: Err, detail: str = "") -> JSONResponse:
    """Build a unified JSON error response.

    Args:
        err: An :class:`Err` enum member.
        detail: Optional extra context appended after the base message
                (e.g. the invalid value).

    Returns:
        A :class:`JSONResponse` with body ``{"code": 401, "error": "..."}``.
    """
    message = f"{err.message}: {detail}" if detail else err.message
    return JSONResponse(
        status_code=err.status,
        content={"code": err.code, "error": message},
    )
