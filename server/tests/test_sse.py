"""Tests for routers/sse.py — SSE endpoint handlers (mock state layer)."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from routers.sse import _authenticate, _resolve_session, _sse_event, _sse_comment, MediaItem


# ── MediaItem validation ───────────────────────────────────────────────────


class TestMediaItemValidation:
    def test_empty_content_rejected(self):
        with pytest.raises(Exception):
            MediaItem(type="url", content="")

    def test_whitespace_content_rejected(self):
        with pytest.raises(Exception):
            MediaItem(type="url", content="   ")

    def test_valid_content_accepted(self):
        item = MediaItem(type="url", content="http://example.com/file.jpg")
        assert item.content == "http://example.com/file.jpg"


# ── Pure helpers ─────────────────────────────────────────────────────────────


class TestSseEvent:
    def test_format(self):
        result = _sse_event("chunk", {"content": "hello"})
        assert result == 'event: chunk\ndata: {"content": "hello"}\n\n'

    def test_unicode(self):
        result = _sse_event("chunk", {"content": "你好"})
        assert "你好" in result
        assert result.startswith("event: chunk\n")

    def test_comment(self):
        assert _sse_comment() == ": heartbeat\n\n"


# ── _authenticate ────────────────────────────────────────────────────────────


class TestAuthenticate:
    async def test_none_header(self):
        assert await _authenticate(None) is None

    async def test_empty_header(self):
        assert await _authenticate("") is None

    async def test_no_bearer_prefix(self):
        assert await _authenticate("Basic abc") is None

    async def test_bearer_empty_token(self):
        assert await _authenticate("Bearer   ") is None

    async def test_valid_token(self):
        with patch("routers.sse.state") as mock_state:
            mock_state.token_manager.validate = AsyncMock(return_value=True)
            result = await _authenticate("Bearer sk-abc123")
            assert result == "sk-abc123"
            mock_state.token_manager.validate.assert_awaited_once_with("sk-abc123")

    async def test_invalid_token(self):
        with patch("routers.sse.state") as mock_state:
            mock_state.token_manager.validate = AsyncMock(return_value=False)
            result = await _authenticate("Bearer sk-expired")
            assert result is None

    async def test_case_insensitive_bearer(self):
        with patch("routers.sse.state") as mock_state:
            mock_state.token_manager.validate = AsyncMock(return_value=True)
            result = await _authenticate("BEARER sk-abc123")
            assert result == "sk-abc123"


# ── _resolve_session ─────────────────────────────────────────────────────────


class TestResolveSession:
    async def test_explicit_session_found(self):
        with patch("routers.sse.state") as mock_state:
            mock_state.bridge.get_session = AsyncMock(
                return_value=("sid-2", 2)
            )
            sid, num = await _resolve_session("tok", "sid-2")
            assert sid == "sid-2"
            assert num == 2
            mock_state.bridge.get_session.assert_awaited_once_with("tok", "sid-2")

    async def test_explicit_session_not_found(self):
        with patch("routers.sse.state") as mock_state:
            mock_state.bridge.get_session = AsyncMock(return_value=None)
            with pytest.raises(ValueError, match="Session not found"):
                await _resolve_session("tok", "nonexistent")

    async def test_no_session_id_creates_new(self):
        with patch("routers.sse.state") as mock_state:
            mock_state.bridge.create_session = AsyncMock(return_value=("new-id", 1))
            sid, num = await _resolve_session("tok", None)
            assert sid == "new-id"
            assert num == 1
            mock_state.bridge.create_session.assert_awaited_once_with("tok")


# ── Endpoint integration (via FastAPI TestClient) ────────────────────────────

# We test the route handlers by importing the router and calling them
# with mocked state, avoiding the need for a full app setup.


@pytest.fixture
def mock_state_valid_token():
    """Mock state with a valid token. Yields the mock_state object."""
    with patch("routers.sse.state") as mock_state:
        mock_state.token_manager.validate = AsyncMock(return_value=True)
        yield mock_state


@pytest.fixture
def mock_state_full(mock_state_valid_token):
    """Mock state with valid token, connected bot, existing session, and send_to_bot stub."""
    ms = mock_state_valid_token
    ms.bridge.is_bot_connected = AsyncMock(return_value=True)
    ms.bridge.get_session = AsyncMock(return_value=("sid-1", 1))
    ms.bridge.create_session = AsyncMock(return_value=("sid-new", 1))
    ms.bridge.send_to_bot = AsyncMock(return_value="req_abc")
    ms.queue = AsyncMock()
    return ms


class TestChatSseEndpoint:
    """Test chat_sse route handler validation paths."""

    async def test_401_no_auth(self):
        with patch("routers.sse.state") as mock_state:
            mock_state.token_manager.validate = AsyncMock(return_value=False)
            from routers.sse import chat_sse, ChatRequest
            body = ChatRequest(content="hello")
            resp = await chat_sse(body, authorization=None)
            assert resp.status_code == 401

    async def test_401_invalid_token(self):
        with patch("routers.sse.state") as mock_state:
            mock_state.token_manager.validate = AsyncMock(return_value=False)
            from routers.sse import chat_sse, ChatRequest
            body = ChatRequest(content="hello")
            resp = await chat_sse(body, authorization="Bearer sk-bad")
            assert resp.status_code == 401

    async def test_400_empty_message(self, mock_state_valid_token):
        from routers.sse import chat_sse, ChatRequest
        body = ChatRequest(content="")
        resp = await chat_sse(body, authorization="Bearer sk-valid")
        assert resp.status_code == 400
        assert "Empty message" in resp.body.decode()

    async def test_400_unsupported_media_type(self, mock_state_valid_token):
        from routers.sse import chat_sse, ChatRequest
        body = ChatRequest(
            content="hello",
            media=[MediaItem(type="base64", content="abc", mimeType="image/png")],
        )
        resp = await chat_sse(body, authorization="Bearer sk-valid")
        assert resp.status_code == 400
        assert "Unsupported media type" in resp.body.decode()

    async def test_400_invalid_url_scheme(self, mock_state_valid_token):
        from routers.sse import chat_sse, ChatRequest
        body = ChatRequest(
            content="hello",
            media=[MediaItem(type="url", content="ftp://bad/file.jpg")],
        )
        resp = await chat_sse(body, authorization="Bearer sk-valid")
        assert resp.status_code == 400
        assert "Invalid media URL scheme" in resp.body.decode()

    async def test_400_bot_not_connected(self, mock_state_valid_token):
        mock_state_valid_token.bridge.is_bot_connected = AsyncMock(return_value=False)
        from routers.sse import chat_sse, ChatRequest
        body = ChatRequest(content="hello")
        resp = await chat_sse(body, authorization="Bearer sk-valid")
        assert resp.status_code == 400
        assert "No bot connected" in resp.body.decode()

    async def test_404_session_not_found(self, mock_state_valid_token):
        mock_state_valid_token.bridge.is_bot_connected = AsyncMock(return_value=True)
        mock_state_valid_token.bridge.get_session = AsyncMock(return_value=None)
        from routers.sse import chat_sse, ChatRequest
        body = ChatRequest(content="hello", sessionId="nonexistent")
        resp = await chat_sse(body, authorization="Bearer sk-valid")
        assert resp.status_code == 404
        assert "Session not found" in resp.body.decode()

    async def test_500_send_to_bot_fails(self, mock_state_full):
        mock_state_full.bridge.send_to_bot = AsyncMock(return_value=None)
        from routers.sse import chat_sse, ChatRequest
        body = ChatRequest(content="hello", sessionId="sid-1")
        resp = await chat_sse(body, authorization="Bearer sk-valid")
        assert resp.status_code == 500
        assert "Failed to send" in resp.body.decode()

    async def test_200_returns_sse_stream(self, mock_state_full):
        from routers.sse import chat_sse, ChatRequest
        body = ChatRequest(content="hello", sessionId="sid-1")
        resp = await chat_sse(body, authorization="Bearer sk-valid")
        assert resp.media_type == "text/event-stream"
        assert resp.headers["Cache-Control"] == "no-cache"

    async def test_inbox_purged_before_send(self, mock_state_full):
        """Stale events in inbox are purged and group is recreated before sending to bot."""
        from routers.sse import chat_sse, ChatRequest
        body = ChatRequest(content="hello", sessionId="sid-1")
        await chat_sse(body, authorization="Bearer sk-valid")
        # Verify inbox was purged and group recreated
        inbox = "bridge:chat_inbox:sk-valid:sid-1"
        mock_state_full.queue.purge.assert_awaited_once_with(inbox)
        mock_state_full.queue.ensure_group.assert_awaited_once_with(inbox, "sse")

    async def test_200_media_only(self, mock_state_full):
        """Media-only message (no text) should be accepted."""
        from routers.sse import chat_sse, ChatRequest
        body = ChatRequest(
            media=[MediaItem(type="url", content="http://host:9000/file.mp3")],
        )
        resp = await chat_sse(body, authorization="Bearer sk-valid")
        assert resp.media_type == "text/event-stream"
        call_kwargs = mock_state_full.bridge.send_to_bot.call_args
        assert call_kwargs[1]["media_urls"] == ["http://host:9000/file.mp3"]

    async def test_200_multi_media(self, mock_state_full):
        """Multiple media items should all be passed through."""
        from routers.sse import chat_sse, ChatRequest
        body = ChatRequest(
            content="compare",
            sessionId="sid-1",
            media=[
                MediaItem(type="url", content="http://host:9000/a.jpg"),
                MediaItem(type="url", content="http://host:9000/b.png"),
            ],
        )
        resp = await chat_sse(body, authorization="Bearer sk-valid")
        assert resp.media_type == "text/event-stream"
        call_kwargs = mock_state_full.bridge.send_to_bot.call_args
        assert call_kwargs[1]["media_urls"] == [
            "http://host:9000/a.jpg",
            "http://host:9000/b.png",
        ]


class TestListSessionsEndpoint:
    async def test_401(self):
        with patch("routers.sse.state") as mock_state:
            mock_state.token_manager.validate = AsyncMock(return_value=False)
            from routers.sse import list_sessions
            resp = await list_sessions(authorization=None)
            assert resp.status_code == 401

    async def test_200(self):
        with patch("routers.sse.state") as mock_state:
            mock_state.token_manager.validate = AsyncMock(return_value=True)
            mock_state.bridge.get_sessions = AsyncMock(
                return_value=[("sid-1", 1), ("sid-2", 2)]
            )
            from routers.sse import list_sessions
            resp = await list_sessions(authorization="Bearer sk-valid")
            assert resp["code"] == 0
            assert len(resp["sessions"]) == 2
            assert "activeSessionId" not in resp


class TestCreateSessionEndpoint:
    async def test_401(self):
        with patch("routers.sse.state") as mock_state:
            mock_state.token_manager.validate = AsyncMock(return_value=False)
            from routers.sse import create_session
            resp = await create_session(authorization=None)
            assert resp.status_code == 401

    async def test_200(self):
        with patch("routers.sse.state") as mock_state:
            mock_state.token_manager.validate = AsyncMock(return_value=True)
            mock_state.bridge.create_session = AsyncMock(return_value=("sid-new", 3))
            mock_state.bridge.get_sessions = AsyncMock(
                return_value=[("sid-1", 1), ("sid-new", 3)]
            )
            from routers.sse import create_session
            resp = await create_session(authorization="Bearer sk-valid")
            assert resp["code"] == 0
            assert resp["sessionId"] == "sid-new"
            assert resp["sessionNumber"] == 3
            assert len(resp["sessions"]) == 2
            assert "activeSessionId" not in resp
