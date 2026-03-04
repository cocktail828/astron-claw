"""Tests for services/bridge.py — ConnectionBridge methods (mock Redis)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.bridge import ConnectionBridge


@pytest.fixture()
def bridge(mock_redis, mock_session_store):
    b = ConnectionBridge(mock_redis, session_store=mock_session_store)
    yield b
    # Cancel any poll tasks created during tests
    for task in b._poll_tasks.values():
        task.cancel()


class TestRegisterBot:
    async def test_register_bot_success(self, bridge, mock_redis):
        ws = AsyncMock()
        mock_redis.sismember.return_value = False
        result = await bridge.register_bot("tok-1", ws)
        assert result is True
        assert "tok-1" in bridge._bots
        mock_redis.sadd.assert_awaited()
        # Poll task should be created
        assert "bot:tok-1" in bridge._poll_tasks

    async def test_register_bot_local_dup(self, bridge, mock_redis):
        ws1, ws2 = AsyncMock(), AsyncMock()
        mock_redis.sismember.return_value = False
        await bridge.register_bot("tok-1", ws1)

        result = await bridge.register_bot("tok-1", ws2)
        assert result is False

    async def test_register_bot_redis_dup(self, bridge, mock_redis):
        ws = AsyncMock()
        mock_redis.sismember.return_value = True
        mock_redis.hget.return_value = "other-worker-id"
        mock_redis.exists.return_value = 1  # owning worker is alive
        result = await bridge.register_bot("tok-remote", ws)
        assert result is False


class TestRegisterChat:
    async def test_register_chat(self, bridge, mock_redis):
        ws = AsyncMock()
        await bridge.register_chat("tok-1", ws, "session-abc")
        assert ws in bridge._chat_sessions
        assert bridge._chat_sessions[ws] == ("tok-1", "session-abc")
        mock_redis.sadd.assert_awaited()
        assert "chat:tok-1:session-abc" in bridge._poll_tasks


class TestSendToBot:
    async def test_send_to_bot_text(self, bridge, mock_redis, mock_session_store):
        ws = AsyncMock()
        mock_redis.sismember.return_value = False
        await bridge.register_bot("tok-1", ws)
        mock_session_store.get_active_session.return_value = "session-id-1"

        req_id = await bridge.send_to_bot("tok-1", "hello", msg_type="text")
        assert req_id is not None
        assert req_id.startswith("req_")

        sent = ws.send_json.call_args[0][0]
        assert sent["method"] == "session/prompt"
        content_items = sent["params"]["prompt"]["content"]
        assert len(content_items) == 1
        assert content_items[0] == {"type": "text", "text": "hello"}

    async def test_send_to_bot_image(self, bridge, mock_redis, mock_session_store):
        ws = AsyncMock()
        mock_redis.sismember.return_value = False
        await bridge.register_bot("tok-1", ws)
        mock_session_store.get_active_session.return_value = "session-id-1"

        media = {
            "mediaId": "media_abc",
            "fileName": "photo.png",
            "mimeType": "image/png",
            "fileSize": 1024,
            "downloadUrl": "/api/media/download/media_abc",
        }
        req_id = await bridge.send_to_bot("tok-1", "my photo", msg_type="image", media=media)
        assert req_id is not None

        sent = ws.send_json.call_args[0][0]
        content_items = sent["params"]["prompt"]["content"]
        assert len(content_items) == 2
        assert content_items[0]["type"] == "text"
        assert content_items[1]["type"] == "media"
        assert content_items[1]["media"]["mediaId"] == "media_abc"


class TestHandleBotMessage:
    async def test_handle_bot_message_invalid_json(self, bridge):
        # Should not raise
        await bridge.handle_bot_message("tok-1", "not json{{{")

    async def test_handle_bot_message_ping(self, bridge):
        # Ping messages should be silently ignored
        await bridge.handle_bot_message("tok-1", json.dumps({"type": "ping"}))


class TestGetConnectionsSummary:
    async def test_get_connections_summary(self, bridge, mock_redis):
        # smembers is called twice: first for online_bots, then for workers SET
        mock_redis.smembers.side_effect = [
            {"tok-1", "tok-2"},   # _ONLINE_BOTS_KEY
            {"worker-a"},         # _WORKERS_KEY
        ]
        mock_redis.hget.return_value = "some-worker"
        mock_redis.exists.return_value = 1  # all workers alive
        # hgetall is called once per alive worker for chat counts
        mock_redis.hgetall.return_value = {"tok-1": "3", "tok-3": "1"}

        summary = await bridge.get_connections_summary()
        assert summary["tok-1"]["bot_online"] is True
        assert summary["tok-1"]["chat_count"] == 3
        assert summary["tok-2"]["bot_online"] is True
        assert summary["tok-2"]["chat_count"] == 0
        assert summary["tok-3"]["bot_online"] is False
        assert summary["tok-3"]["chat_count"] == 1


class TestSessionCreateSwitch:
    async def test_create_session(self, bridge, mock_session_store):
        mock_session_store.create_session.return_value = 1
        session_id, number = await bridge.create_session("tok-1")
        assert number == 1
        assert session_id  # non-empty UUID string
        mock_session_store.create_session.assert_awaited_once_with("tok-1", session_id)

    async def test_switch_session_success(self, bridge, mock_session_store):
        mock_session_store.switch_session.return_value = True
        assert await bridge.switch_session("tok-1", "some-session") is True
        mock_session_store.switch_session.assert_awaited_once_with("tok-1", "some-session")

    async def test_switch_session_failure(self, bridge, mock_session_store):
        mock_session_store.switch_session.return_value = False
        assert await bridge.switch_session("tok-1", "nonexistent") is False

    async def test_get_sessions(self, bridge, mock_session_store):
        mock_session_store.get_sessions.return_value = (
            [("sid-1", 1), ("sid-2", 2)], "sid-2"
        )
        sessions, active = await bridge.get_sessions("tok-1")
        assert len(sessions) == 2
        assert sessions[0] == ("sid-1", 1)
        assert active == "sid-2"
        mock_session_store.get_sessions.assert_awaited_once_with("tok-1")

    async def test_get_active_session(self, bridge, mock_session_store):
        mock_session_store.get_active_session.return_value = "sid-1"
        result = await bridge.get_active_session("tok-1")
        assert result == "sid-1"

    async def test_cleanup_old_sessions(self, bridge, mock_session_store):
        mock_session_store.cleanup_old_sessions.return_value = 5
        result = await bridge.cleanup_old_sessions(max_age_days=30)
        assert result == 5
        mock_session_store.cleanup_old_sessions.assert_awaited_once_with(30 * 86400)
