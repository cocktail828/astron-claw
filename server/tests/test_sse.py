"""Tests for routers/sse.py — SSE endpoint handlers (mock state layer)."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from routers.sse import _resolve_session, _sse_event, _sse_comment, _stream_response, MediaItem


# ── Helpers ──────────────────────────────────────────────────────────────────

class _FakeState:
    """Minimal stand-in for ``request.state`` with a ``token`` attribute."""
    def __init__(self, token: str):
        self.token = token


class _FakeRequest:
    """Minimal stand-in for ``fastapi.Request`` used by route handlers."""
    def __init__(self, token: str = "sk-valid"):
        self.state = _FakeState(token)


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
        yield mock_state


@pytest.fixture
def mock_state_full(mock_state_valid_token):
    """Mock state with connected bot, existing session, and send_to_bot stub."""
    ms = mock_state_valid_token
    ms.bridge.is_bot_connected = AsyncMock(return_value=True)
    ms.bridge.get_session = AsyncMock(return_value=("sid-1", 1))
    ms.bridge.create_session = AsyncMock(return_value=("sid-new", 1))
    ms.bridge.send_to_bot = AsyncMock(return_value="req_abc")
    ms.queue = AsyncMock()
    return ms


class TestChatSseEndpoint:
    """Test chat_sse route handler validation paths.

    Auth (401) is now handled by TokenAuthMiddleware and tested in
    test_token_auth.py.  These tests assume the middleware already
    validated the token and set ``request.state.token``.
    """

    async def test_400_empty_message(self, mock_state_valid_token):
        from routers.sse import chat_sse, ChatRequest
        body = ChatRequest(content="")
        resp = await chat_sse(body, request=_FakeRequest())
        assert resp.status_code == 400
        assert "Empty message" in resp.body.decode()

    async def test_400_unsupported_media_type(self, mock_state_valid_token):
        from routers.sse import chat_sse, ChatRequest
        body = ChatRequest(
            content="hello",
            media=[MediaItem(type="base64", content="abc", mimeType="image/png")],
        )
        resp = await chat_sse(body, request=_FakeRequest())
        assert resp.status_code == 400
        assert "Unsupported media type" in resp.body.decode()

    async def test_400_invalid_url_scheme(self, mock_state_valid_token):
        from routers.sse import chat_sse, ChatRequest
        body = ChatRequest(
            content="hello",
            media=[MediaItem(type="url", content="ftp://bad/file.jpg")],
        )
        resp = await chat_sse(body, request=_FakeRequest())
        assert resp.status_code == 400
        assert "Invalid media URL scheme" in resp.body.decode()

    async def test_400_bot_not_connected(self, mock_state_valid_token):
        mock_state_valid_token.bridge.is_bot_connected = AsyncMock(return_value=False)
        from routers.sse import chat_sse, ChatRequest
        body = ChatRequest(content="hello")
        resp = await chat_sse(body, request=_FakeRequest())
        assert resp.status_code == 400
        assert "No bot connected" in resp.body.decode()

    async def test_404_session_not_found(self, mock_state_valid_token):
        mock_state_valid_token.bridge.is_bot_connected = AsyncMock(return_value=True)
        mock_state_valid_token.bridge.get_session = AsyncMock(return_value=None)
        from routers.sse import chat_sse, ChatRequest
        body = ChatRequest(content="hello", sessionId="nonexistent")
        resp = await chat_sse(body, request=_FakeRequest())
        assert resp.status_code == 404
        assert "Session not found" in resp.body.decode()

    async def test_500_send_to_bot_fails(self, mock_state_full):
        mock_state_full.bridge.send_to_bot = AsyncMock(return_value=None)
        from routers.sse import chat_sse, ChatRequest
        body = ChatRequest(content="hello", sessionId="sid-1")
        resp = await chat_sse(body, request=_FakeRequest())
        assert resp.status_code == 500
        assert "Failed to send" in resp.body.decode()

    async def test_200_returns_sse_stream(self, mock_state_full):
        from routers.sse import chat_sse, ChatRequest
        body = ChatRequest(content="hello", sessionId="sid-1")
        resp = await chat_sse(body, request=_FakeRequest())
        assert resp.media_type == "text/event-stream"
        assert resp.headers["Cache-Control"] == "no-cache"

    async def test_inbox_purged_before_send(self, mock_state_full):
        """Stale events in inbox are purged and group is recreated before sending to bot."""
        from routers.sse import chat_sse, ChatRequest
        body = ChatRequest(content="hello", sessionId="sid-1")
        await chat_sse(body, request=_FakeRequest())
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
        resp = await chat_sse(body, request=_FakeRequest())
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
        resp = await chat_sse(body, request=_FakeRequest())
        assert resp.media_type == "text/event-stream"
        call_kwargs = mock_state_full.bridge.send_to_bot.call_args
        assert call_kwargs[1]["media_urls"] == [
            "http://host:9000/a.jpg",
            "http://host:9000/b.png",
        ]


class TestListSessionsEndpoint:
    async def test_200(self):
        with patch("routers.sse.state") as mock_state:
            mock_state.bridge.get_sessions = AsyncMock(
                return_value=[("sid-1", 1), ("sid-2", 2)]
            )
            from routers.sse import list_sessions
            resp = await list_sessions(request=_FakeRequest())
            assert resp["code"] == 0
            assert len(resp["sessions"]) == 2
            assert "activeSessionId" not in resp


# ── _stream_response chunk auto-injection ────────────────────────────────


class TestStreamResponseChunkInjection:
    """Verify that done events with content auto-inject a preceding chunk."""

    async def _collect_events(self, messages, max_consume_calls=50):
        """Helper: mock queue to return given messages then None, collect SSE events."""
        call_count = 0

        async def fake_consume(inbox, group, consumer, block_ms):
            nonlocal call_count
            if call_count < len(messages):
                msg = messages[call_count]
                call_count += 1
                return (f"id-{call_count}", json.dumps(msg))
            call_count += 1
            if call_count > max_consume_calls:
                raise TimeoutError("fake_consume safety limit reached")
            return None

        with patch("routers.sse.state") as mock_state:
            mock_queue = AsyncMock()
            mock_queue.consume = AsyncMock(side_effect=fake_consume)
            mock_state.queue = mock_queue

            events = []
            async for ev in _stream_response("tok", "sid", 1, "req"):
                events.append(ev)
                # Stop after terminal event
                if "event: done" in ev or "event: error" in ev:
                    break
            return events

    async def test_done_with_content_no_preceding_chunks_injects_chunk(self):
        """When done carries content and no chunk was sent, a chunk is auto-injected."""
        events = await self._collect_events([
            {"type": "done", "content": "Model list: gpt-4"},
        ])
        # events[0] = session, events[1] = injected chunk, events[2] = done
        event_types = [e.split("\n")[0] for e in events]
        assert event_types[1] == "event: chunk"
        assert '"Model list: gpt-4"' in events[1]
        assert event_types[2] == "event: done"
        assert '"Model list: gpt-4"' in events[2]

    async def test_done_with_content_after_chunks_no_extra_injection(self):
        """When chunks already preceded done, no extra chunk is injected."""
        events = await self._collect_events([
            {"type": "chunk", "content": "partial"},
            {"type": "done", "content": "full answer"},
        ])
        # events[0] = session, events[1] = chunk, events[2] = done (no extra chunk)
        event_types = [e.split("\n")[0] for e in events]
        assert event_types == [
            "event: session",
            "event: chunk",
            "event: done",
        ]

    async def test_done_with_empty_string_content_no_injection(self):
        """When done has empty string content, no chunk is injected."""
        events = await self._collect_events([
            {"type": "done", "content": ""},
        ])
        event_types = [e.split("\n")[0] for e in events]
        assert event_types == [
            "event: session",
            "event: done",
        ]

    async def test_done_without_content_no_injection(self):
        """When done has no content, no chunk is injected even without prior chunks."""
        events = await self._collect_events([
            {"type": "done"},
        ])
        event_types = [e.split("\n")[0] for e in events]
        assert event_types == [
            "event: session",
            "event: done",
        ]


class TestCreateSessionEndpoint:
    async def test_200(self):
        with patch("routers.sse.state") as mock_state:
            mock_state.bridge.create_session = AsyncMock(return_value=("sid-new", 3))
            mock_state.bridge.get_sessions = AsyncMock(
                return_value=[("sid-1", 1), ("sid-new", 3)]
            )
            from routers.sse import create_session
            resp = await create_session(request=_FakeRequest())
            assert resp["code"] == 0
            assert resp["sessionId"] == "sid-new"
            assert resp["sessionNumber"] == 3
            assert len(resp["sessions"]) == 2
            assert "activeSessionId" not in resp
