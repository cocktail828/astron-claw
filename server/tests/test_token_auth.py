"""Tests for infra/token_auth.py — TokenAuthMiddleware."""

from unittest.mock import AsyncMock, patch

import pytest

from infra.token_auth import _extract_bearer, TokenAuthMiddleware


# ── _extract_bearer ──────────────────────────────────────────────────────────


class TestExtractBearer:
    def test_none(self):
        assert _extract_bearer(None) is None

    def test_empty(self):
        assert _extract_bearer("") is None

    def test_no_bearer_prefix(self):
        assert _extract_bearer("Basic abc") is None

    def test_bearer_empty_token(self):
        assert _extract_bearer("Bearer   ") is None

    def test_valid(self):
        assert _extract_bearer("Bearer sk-abc123") == "sk-abc123"

    def test_case_insensitive(self):
        assert _extract_bearer("BEARER sk-abc123") == "sk-abc123"

    def test_extra_whitespace(self):
        assert _extract_bearer("Bearer   sk-abc123  ") == "sk-abc123"


# ── TokenAuthMiddleware ──────────────────────────────────────────────────────


def _make_scope(path: str, headers: list[tuple[bytes, bytes]] | None = None):
    return {
        "type": "http",
        "path": path,
        "headers": headers or [],
    }


def _mock_redis(cache_hit: bool = False):
    """Return a mock Redis that simulates cache hit/miss."""
    mock = AsyncMock()
    mock.get = AsyncMock(return_value="1" if cache_hit else None)
    mock.set = AsyncMock()
    return mock


class TestTokenAuthMiddleware:
    """Test the ASGI middleware directly by capturing scope/response."""

    async def _invoke(self, scope, *, validate_return=True, cache_hit=False):
        """Run the middleware and return (status_code, passed_scope, mock_redis, mock_state).

        If the middleware calls the inner app, *passed_scope* is the scope
        it received; otherwise it is ``None`` (middleware short-circuited).
        """
        captured_scope = None
        status_code = None

        async def inner_app(sc, receive, send):
            nonlocal captured_scope
            captured_scope = sc

        async def receive():
            return {"type": "http.request", "body": b""}

        sent_parts = []

        async def send(msg):
            nonlocal status_code
            sent_parts.append(msg)
            if msg["type"] == "http.response.start":
                status_code = msg["status"]

        mock_redis = _mock_redis(cache_hit=cache_hit)

        with patch("infra.token_auth.state") as mock_state, \
             patch("infra.token_auth.get_redis", return_value=mock_redis):
            mock_state.token_manager.validate = AsyncMock(return_value=validate_return)
            mw = TokenAuthMiddleware(inner_app)
            await mw(scope, receive, send)

        return status_code, captured_scope, mock_redis, mock_state

    # ── Non-protected paths pass through ──────────────────────────────────

    async def test_unprotected_path_passes_through(self):
        scope = _make_scope("/api/token")
        status, passed, *_ = await self._invoke(scope)
        assert status is None  # inner app was called, no HTTP response
        assert passed is not None

    async def test_non_http_passes_through(self):
        scope = {"type": "websocket", "path": "/bridge/chat", "headers": []}
        status, passed, *_ = await self._invoke(scope)
        assert passed is not None

    # ── Protected path — missing auth ─────────────────────────────────────

    async def test_protected_no_header_returns_401(self):
        scope = _make_scope("/bridge/chat")
        status, passed, *_ = await self._invoke(scope, validate_return=False)
        assert status == 401
        assert passed is None

    # ── Protected path — invalid token ────────────────────────────────────

    async def test_protected_invalid_token_returns_401(self):
        scope = _make_scope("/bridge/chat", headers=[
            (b"authorization", b"Bearer sk-bad"),
        ])
        status, passed, *_ = await self._invoke(scope, validate_return=False)
        assert status == 401
        assert passed is None

    # ── Protected path — valid Authorization ──────────────────────────────

    async def test_protected_valid_authorization(self):
        scope = _make_scope("/bridge/chat", headers=[
            (b"authorization", b"Bearer sk-good"),
        ])
        status, passed, *_ = await self._invoke(scope, validate_return=True)
        assert status is None
        assert passed is not None
        assert passed["state"]["token"] == "sk-good"

    # ── Protected path — valid X-Api-Key ──────────────────────────────────

    async def test_protected_valid_x_api_key(self):
        scope = _make_scope("/api/media/upload", headers=[
            (b"x-api-key", b"Bearer sk-key"),
        ])
        status, passed, *_ = await self._invoke(scope, validate_return=True)
        assert status is None
        assert passed is not None
        assert passed["state"]["token"] == "sk-key"

    # ── X-Api-Key used when Authorization absent ──────────────────────────

    async def test_x_api_key_used_when_no_authorization(self):
        scope = _make_scope("/bridge/chat/sessions", headers=[
            (b"x-api-key", b"Bearer sk-fromkey"),
        ])
        status, passed, *_ = await self._invoke(scope, validate_return=True)
        assert passed["state"]["token"] == "sk-fromkey"

    # ── Authorization used when X-Api-Key absent ──────────────────────────

    async def test_authorization_used_when_no_x_api_key(self):
        scope = _make_scope("/bridge/chat/sessions", headers=[
            (b"authorization", b"Bearer sk-fromauth"),
        ])
        status, passed, *_ = await self._invoke(scope, validate_return=True)
        assert passed["state"]["token"] == "sk-fromauth"

    # ── X-Api-Key invalid token returns 401 ───────────────────────────────

    async def test_x_api_key_invalid_token_returns_401(self):
        scope = _make_scope("/bridge/chat", headers=[
            (b"x-api-key", b"Bearer sk-bad"),
        ])
        status, passed, *_ = await self._invoke(scope, validate_return=False)
        assert status == 401
        assert passed is None

    # ── X-Api-Key without Bearer prefix returns 401 ───────────────────────

    async def test_x_api_key_no_bearer_prefix_returns_401(self):
        scope = _make_scope("/bridge/chat", headers=[
            (b"x-api-key", b"sk-raw-token"),
        ])
        status, passed, *_ = await self._invoke(scope, validate_return=True)
        assert status == 401
        assert passed is None

    # ── Both headers present — X-Api-Key wins ─────────────────────────────

    async def test_x_api_key_preferred_when_both_present(self):
        scope = _make_scope("/bridge/chat", headers=[
            (b"authorization", b"Bearer sk-from-auth"),
            (b"x-api-key", b"Bearer sk-from-key"),
        ])
        status, passed, *_ = await self._invoke(scope, validate_return=True)
        assert passed is not None
        assert passed["state"]["token"] == "sk-from-key"

    # ── Redis cache hit skips MySQL ───────────────────────────────────────

    async def test_cache_hit_skips_mysql(self):
        scope = _make_scope("/bridge/chat", headers=[
            (b"authorization", b"Bearer sk-cached"),
        ])
        status, passed, mock_redis, mock_state = await self._invoke(
            scope, validate_return=True, cache_hit=True,
        )
        assert passed is not None
        assert passed["state"]["token"] == "sk-cached"
        # Redis was queried
        mock_redis.get.assert_awaited_once()
        # MySQL was NOT queried
        mock_state.token_manager.validate.assert_not_awaited()

    # ── Redis cache miss falls through to MySQL and caches ────────────────

    async def test_cache_miss_queries_mysql_and_caches(self):
        scope = _make_scope("/bridge/chat", headers=[
            (b"authorization", b"Bearer sk-new"),
        ])
        status, passed, mock_redis, mock_state = await self._invoke(
            scope, validate_return=True, cache_hit=False,
        )
        assert passed is not None
        # MySQL was queried
        mock_state.token_manager.validate.assert_awaited_once_with("sk-new")
        # Result was cached in Redis
        mock_redis.set.assert_awaited_once()
