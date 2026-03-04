"""Tests for services/bridge.py — ConnectionBridge methods (mock Redis)."""

import asyncio
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


# ── Cross-worker inbox tests ─────────────────────────────────────────────────

class TestSendToBotRemote:
    async def test_writes_to_bot_inbox_when_no_local_bot(self, bridge, mock_redis, mock_session_store):
        """When bot is not on this worker, message is pushed to bot_inbox:{token}."""
        mock_session_store.get_active_session.return_value = "session-1"
        # No bot registered locally → remote path
        req_id = await bridge.send_to_bot("tok-1", "hello")
        assert req_id is not None

        mock_redis.rpush.assert_awaited_once()
        inbox_key, payload_str = mock_redis.rpush.call_args[0]
        assert inbox_key == "bridge:bot_inbox:tok-1"
        data = json.loads(payload_str)
        assert data["rpc_request"]["method"] == "session/prompt"
        assert data["rpc_request"]["params"]["prompt"]["content"][0]["text"] == "hello"
        # TTL should be set on the inbox key
        mock_redis.expire.assert_awaited()


class TestBroadcastToRemoteChats:
    async def test_pushes_to_remote_sessions_only(self, bridge, mock_redis):
        """Skips local sessions; pushes event to remote sessions' inboxes."""
        chat_ws = AsyncMock()
        await bridge.register_chat("tok-1", chat_ws, "session-local")
        mock_redis.rpush.reset_mock()  # clear any calls from register_chat

        mock_redis.smembers.return_value = {"session-local", "session-remote"}

        await bridge._broadcast_to_remote_chats("tok-1", {"type": "chunk", "content": "hi"})

        pushed_keys = [call[0][0] for call in mock_redis.rpush.call_args_list]
        assert "bridge:chat_inbox:tok-1:session-remote" in pushed_keys
        assert "bridge:chat_inbox:tok-1:session-local" not in pushed_keys

    async def test_no_push_when_no_active_chats(self, bridge, mock_redis):
        """No RPUSH when active_chats SET is empty."""
        mock_redis.smembers.return_value = set()
        await bridge._broadcast_to_remote_chats("tok-1", {"type": "chunk"})
        mock_redis.rpush.assert_not_awaited()


class TestPollBotInbox:
    async def test_forwards_rpc_request_to_bot_ws(self, bridge, mock_redis):
        """_poll_bot_inbox reads one message and forwards rpc_request to bot WS."""
        bot_ws = AsyncMock()
        bridge._bots["tok-1"] = bot_ws  # inject directly, skip register_bot

        rpc_req = {"jsonrpc": "2.0", "id": "req_1", "method": "session/prompt", "params": {}}
        payload = json.dumps({"rpc_request": rpc_req})
        mock_redis.lpop.side_effect = [payload, asyncio.CancelledError()]

        await bridge._poll_bot_inbox("tok-1")

        bot_ws.send_json.assert_awaited_once_with(rpc_req)

    async def test_skips_when_inbox_empty(self, bridge, mock_redis):
        """When inbox is empty, lpop returns None and loop sleeps."""
        bridge._bots["tok-1"] = AsyncMock()
        mock_redis.lpop.side_effect = [None, asyncio.CancelledError()]

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await bridge._poll_bot_inbox("tok-1")

        bridge._bots["tok-1"].send_json.assert_not_awaited()


class TestPollChatInbox:
    async def test_forwards_event_to_chat_ws(self, bridge, mock_redis):
        """_poll_chat_inbox reads one message and sends it to chat WS."""
        chat_ws = AsyncMock()
        payload = json.dumps({"type": "chunk", "content": "hello"})
        mock_redis.lpop.side_effect = [payload, asyncio.CancelledError()]

        await bridge._poll_chat_inbox("tok-1", "session-1", chat_ws)

        chat_ws.send_text.assert_awaited_once_with(payload)

    async def test_skips_when_inbox_empty(self, bridge, mock_redis):
        """When inbox is empty, lpop returns None and loop sleeps."""
        chat_ws = AsyncMock()
        mock_redis.lpop.side_effect = [None, asyncio.CancelledError()]

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await bridge._poll_chat_inbox("tok-1", "session-1", chat_ws)

        chat_ws.send_text.assert_not_awaited()


class TestUnregisterChat:
    async def test_cleans_up_inbox_and_active_chats(self, bridge, mock_redis):
        """Unregistering a chat removes its session from active_chats and deletes inbox."""
        chat_ws = AsyncMock()
        await bridge.register_chat("tok-1", chat_ws, "session-abc")
        mock_redis.reset_mock()

        await bridge.unregister_chat("tok-1", chat_ws)

        mock_redis.srem.assert_awaited_once_with("bridge:active_chats:tok-1", "session-abc")
        mock_redis.delete.assert_awaited_once_with("bridge:chat_inbox:tok-1:session-abc")
        assert chat_ws not in bridge._chat_sessions
        assert "chat:tok-1:session-abc" not in bridge._poll_tasks
