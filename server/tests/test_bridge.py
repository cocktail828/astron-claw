"""Tests for services/bridge.py — ConnectionBridge methods (mock Redis + mock Queue)."""

import asyncio
import json
import time
from unittest.mock import AsyncMock, patch

import pytest

from services.bridge import ConnectionBridge


@pytest.fixture()
def bridge(mock_redis, mock_session_store, mock_queue):
    b = ConnectionBridge(mock_redis, session_store=mock_session_store, queue=mock_queue)
    yield b
    # Cancel any consume tasks created during tests
    for task in b._poll_tasks.values():
        task.cancel()


class TestRegisterBot:
    async def test_register_bot_success(self, bridge, mock_redis, mock_queue):
        ws = AsyncMock()
        mock_redis.zadd.return_value = 1  # ZADD NX succeeds
        result = await bridge.register_bot("tok-1", ws)
        assert result is True
        assert "tok-1" in bridge._bots
        mock_redis.zadd.assert_awaited()
        mock_queue.ensure_group.assert_awaited_once_with("bridge:bot_inbox:tok-1", "bot")
        # Consume task should be created
        assert "bot:tok-1" in bridge._poll_tasks

    async def test_register_bot_local_dup(self, bridge, mock_redis):
        ws1, ws2 = AsyncMock(), AsyncMock()
        mock_redis.zadd.return_value = 1
        await bridge.register_bot("tok-1", ws1)

        result = await bridge.register_bot("tok-1", ws2)
        assert result is False

    async def test_register_bot_remote_alive(self, bridge, mock_redis):
        """Reject registration when another worker has a live bot (ZADD NX fails + fresh score)."""
        ws = AsyncMock()
        mock_redis.zadd.return_value = 0  # ZADD NX fails — member exists
        mock_redis.zscore.return_value = time.time() - 5  # fresh (within 30s)
        result = await bridge.register_bot("tok-remote", ws)
        assert result is False
        assert "tok-remote" not in bridge._bots

    async def test_register_bot_remote_expired(self, bridge, mock_redis, mock_queue):
        """Allow registration when ZADD NX fails but existing heartbeat is stale."""
        ws = AsyncMock()
        # First zadd (NX) fails, second zadd (force overwrite) succeeds
        mock_redis.zadd.side_effect = [0, 1]
        mock_redis.zscore.return_value = time.time() - 60  # stale (>30s)
        result = await bridge.register_bot("tok-stale", ws)
        assert result is True
        assert "tok-stale" in bridge._bots
        # ZADD called twice: first NX attempt, then force overwrite
        assert mock_redis.zadd.await_count == 2

    async def test_register_bot_atomic_prevents_race(self, bridge, mock_redis):
        """When ZADD NX fails and score is fresh, second worker is rejected."""
        ws = AsyncMock()
        mock_redis.zadd.return_value = 0  # NX fails
        mock_redis.zscore.return_value = time.time() - 1  # very fresh
        result = await bridge.register_bot("tok-race", ws)
        assert result is False
        assert "tok-race" not in bridge._bots


class TestSendToBot:
    async def test_send_to_bot_text(self, bridge, mock_queue):
        req_id = await bridge.send_to_bot("tok-1", "hello", session_id="session-id-1")
        assert req_id is not None
        assert req_id.startswith("req_")

        mock_queue.publish.assert_awaited_once()
        inbox_key, payload_str = mock_queue.publish.call_args[0]
        assert inbox_key == "bridge:bot_inbox:tok-1"
        data = json.loads(payload_str)
        sent = data["rpc_request"]
        assert sent["method"] == "session/prompt"
        content_items = sent["params"]["prompt"]["content"]
        assert len(content_items) == 1
        assert content_items[0] == {"type": "text", "content": "hello"}

    async def test_send_to_bot_single_media(self, bridge, mock_queue):
        media_urls = ["http://localhost:9000/astron-claw-media/sid/photo.png"]
        req_id = await bridge.send_to_bot("tok-1", "my photo", media_urls=media_urls, session_id="session-id-1")
        assert req_id is not None

        inbox_key, payload_str = mock_queue.publish.call_args[0]
        data = json.loads(payload_str)
        sent = data["rpc_request"]
        content_items = sent["params"]["prompt"]["content"]
        assert len(content_items) == 2
        assert content_items[0]["type"] == "text"
        assert content_items[0]["content"] == "my photo"
        assert content_items[1]["type"] == "url"
        assert content_items[1]["content"] == "http://localhost:9000/astron-claw-media/sid/photo.png"

    async def test_send_to_bot_multi_media(self, bridge, mock_queue):
        media_urls = [
            "http://localhost:9000/astron-claw-media/sid/photo1.jpg",
            "http://localhost:9000/astron-claw-media/sid/photo2.png",
        ]
        req_id = await bridge.send_to_bot("tok-1", "compare these", media_urls=media_urls, session_id="session-id-1")
        assert req_id is not None

        inbox_key, payload_str = mock_queue.publish.call_args[0]
        data = json.loads(payload_str)
        content_items = data["rpc_request"]["params"]["prompt"]["content"]
        assert len(content_items) == 3
        assert content_items[0] == {"type": "text", "content": "compare these"}
        assert content_items[1]["content"].endswith("/photo1.jpg")
        assert content_items[2]["content"].endswith("/photo2.png")

    async def test_send_to_bot_media_only(self, bridge, mock_queue):
        """Media without text content should produce only media items."""
        media_urls = ["http://localhost:9000/astron-claw-media/sid/voice.mp3"]
        req_id = await bridge.send_to_bot("tok-1", "", media_urls=media_urls, session_id="session-id-1")
        assert req_id is not None

        inbox_key, payload_str = mock_queue.publish.call_args[0]
        data = json.loads(payload_str)
        content_items = data["rpc_request"]["params"]["prompt"]["content"]
        assert len(content_items) == 1
        assert content_items[0]["type"] == "url"
        assert content_items[0]["content"].endswith("/voice.mp3")

    async def test_send_to_bot_unicode_url_encoded(self, bridge, mock_queue):
        """Unicode characters in media URL should be percent-encoded."""
        media_urls = ["http://localhost:9000/astron-claw-media/sid/照片.jpg"]
        req_id = await bridge.send_to_bot("tok-1", "check", media_urls=media_urls, session_id="session-id-1")
        assert req_id is not None

        inbox_key, payload_str = mock_queue.publish.call_args[0]
        data = json.loads(payload_str)
        content_items = data["rpc_request"]["params"]["prompt"]["content"]
        download_url = content_items[1]["content"]
        # Unicode should be percent-encoded
        assert "照片" not in download_url
        assert "%E7%85%A7%E7%89%87" in download_url

    async def test_send_to_bot_requires_session_id(self, bridge, mock_queue):
        """send_to_bot returns None when session_id is not provided."""
        req_id = await bridge.send_to_bot("tok-1", "hello")
        assert req_id is None
        mock_queue.publish.assert_not_awaited()

    async def test_send_to_bot_no_pending_requests(self, bridge, mock_queue):
        """send_to_bot should not store any process-local pending request state."""
        await bridge.send_to_bot("tok-1", "hello", session_id="session-id-1")
        assert not hasattr(bridge, "_pending_requests")


class TestHandleBotMessage:
    async def test_handle_bot_message_invalid_json(self, bridge):
        # Should not raise
        await bridge.handle_bot_message("tok-1", "not json{{{")

    async def test_handle_bot_message_ping(self, bridge):
        # Ping messages should reply with pong to keep reverse-proxy alive
        mock_ws = AsyncMock()
        bridge._bots["tok-1"] = mock_ws
        await bridge.handle_bot_message("tok-1", json.dumps({"type": "ping"}))
        mock_ws.send_text.assert_awaited_once_with("pong")

    async def test_routes_chunk_to_session_from_params(self, bridge, mock_redis, mock_queue):
        """Streaming notifications with sessionId in params are routed to that session's inbox."""
        mock_redis.exists.return_value = 1  # SSE consumer active
        msg = {
            "method": "session/update",
            "params": {
                "sessionId": "session-abc",
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"text": "hello"},
                },
            },
        }
        await bridge.handle_bot_message("tok-1", json.dumps(msg))
        mock_queue.publish.assert_awaited_once()
        inbox_key = mock_queue.publish.call_args[0][0]
        assert inbox_key == "bridge:chat_inbox:tok-1:session-abc"
        payload = json.loads(mock_queue.publish.call_args[0][1])
        assert payload["type"] == "chunk"
        assert payload["content"] == "hello"

    async def test_routes_result_with_session_id(self, bridge, mock_queue):
        """JSON-RPC result with sessionId is logged but does NOT push a done event."""
        msg = {
            "jsonrpc": "2.0",
            "id": "req_123",
            "sessionId": "session-xyz",
            "result": {"stopReason": "end_turn"},
        }
        await bridge.handle_bot_message("tok-1", json.dumps(msg))
        mock_queue.publish.assert_not_awaited()

    async def test_routes_error_with_session_id(self, bridge, mock_redis, mock_queue):
        """JSON-RPC error with sessionId is routed to that session's chat inbox."""
        mock_redis.exists.return_value = 1  # SSE consumer active
        msg = {
            "jsonrpc": "2.0",
            "id": "req_456",
            "sessionId": "session-xyz",
            "error": {"code": -1, "message": "Bot failed"},
        }
        await bridge.handle_bot_message("tok-1", json.dumps(msg))
        mock_queue.publish.assert_awaited_once()
        inbox_key = mock_queue.publish.call_args[0][0]
        assert inbox_key == "bridge:chat_inbox:tok-1:session-xyz"
        payload = json.loads(mock_queue.publish.call_args[0][1])
        assert payload["type"] == "error"
        assert payload["content"] == "Bot failed"

    async def test_error_without_session_id_not_routed(self, bridge, mock_queue):
        """JSON-RPC error without sessionId cannot be routed — logged as warning."""
        msg = {
            "jsonrpc": "2.0",
            "id": "req_789",
            "error": {"code": -1, "message": "Old bot error"},
        }
        await bridge.handle_bot_message("tok-1", json.dumps(msg))
        mock_queue.publish.assert_not_awaited()

    async def test_result_without_session_id_still_logs(self, bridge, mock_queue):
        """JSON-RPC result without sessionId is logged (backward compat with old plugins)."""
        msg = {
            "jsonrpc": "2.0",
            "id": "req_old",
            "result": {"stopReason": "end_turn"},
        }
        # Should not raise
        await bridge.handle_bot_message("tok-1", json.dumps(msg))
        mock_queue.publish.assert_not_awaited()


class TestGetConnectionsSummary:
    async def test_get_connections_summary(self, bridge, mock_redis):
        mock_redis.zrangebyscore.return_value = ["tok-1", "tok-2"]
        summary = await bridge.get_connections_summary()
        assert summary["tok-1"]["bot_online"] is True
        assert summary["tok-2"]["bot_online"] is True
        mock_redis.zrangebyscore.assert_awaited_once()

    async def test_get_connections_summary_empty(self, bridge, mock_redis):
        mock_redis.zrangebyscore.return_value = []
        summary = await bridge.get_connections_summary()
        assert summary == {}


class TestIsBotConnected:
    async def test_bot_connected(self, bridge, mock_redis):
        mock_redis.zscore.return_value = time.time() - 5  # fresh
        assert await bridge.is_bot_connected("tok-1") is True

    async def test_bot_expired(self, bridge, mock_redis):
        mock_redis.zscore.return_value = time.time() - 60  # stale
        assert await bridge.is_bot_connected("tok-1") is False

    async def test_bot_not_found(self, bridge, mock_redis):
        mock_redis.zscore.return_value = None
        assert await bridge.is_bot_connected("tok-1") is False


class TestSessionCreate:
    async def test_create_session(self, bridge, mock_session_store):
        mock_session_store.create_session.return_value = 1
        session_id, number = await bridge.create_session("tok-1")
        assert number == 1
        assert session_id  # non-empty UUID string
        mock_session_store.create_session.assert_awaited_once_with("tok-1", session_id)

    async def test_get_session_found(self, bridge, mock_session_store):
        mock_session_store.get_session.return_value = ("sid-1", 1)
        result = await bridge.get_session("tok-1", "sid-1")
        assert result == ("sid-1", 1)
        mock_session_store.get_session.assert_awaited_once_with("tok-1", "sid-1")

    async def test_get_session_not_found(self, bridge, mock_session_store):
        mock_session_store.get_session.return_value = None
        result = await bridge.get_session("tok-1", "nonexistent")
        assert result is None

    async def test_get_sessions(self, bridge, mock_session_store):
        mock_session_store.get_sessions.return_value = [("sid-1", 1), ("sid-2", 2)]
        sessions = await bridge.get_sessions("tok-1")
        assert len(sessions) == 2
        assert sessions[0] == ("sid-1", 1)
        mock_session_store.get_sessions.assert_awaited_once_with("tok-1")

    async def test_cleanup_old_sessions(self, bridge, mock_session_store):
        mock_session_store.cleanup_old_sessions.return_value = 5
        result = await bridge.cleanup_old_sessions(max_age_days=30)
        assert result == 5
        mock_session_store.cleanup_old_sessions.assert_awaited_once_with(30 * 86400)


# ── Cross-worker inbox tests ─────────────────────────────────────────────────

class TestSendToBotRemote:
    async def test_writes_to_bot_inbox_when_no_local_bot(self, bridge, mock_queue):
        """When bot is not on this worker, message is pushed to bot_inbox:{token}."""
        req_id = await bridge.send_to_bot("tok-1", "hello", session_id="session-1")
        assert req_id is not None

        mock_queue.publish.assert_awaited_once()
        inbox_key, payload_str = mock_queue.publish.call_args[0]
        assert inbox_key == "bridge:bot_inbox:tok-1"
        data = json.loads(payload_str)
        assert data["rpc_request"]["method"] == "session/prompt"
        assert data["rpc_request"]["params"]["prompt"]["content"][0]["content"] == "hello"


class TestBotStatusLogging:
    def test_notify_bot_connected_is_sync(self, bridge):
        """notify_bot_connected is now a sync log-only method."""
        bridge.notify_bot_connected("tok-1")  # should not raise

    def test_notify_bot_disconnected_is_sync(self, bridge):
        """notify_bot_disconnected is now a sync log-only method."""
        bridge.notify_bot_disconnected("tok-1")  # should not raise


class TestPollBotInbox:
    async def test_forwards_rpc_request_to_bot_ws(self, bridge, mock_queue):
        """_poll_bot_inbox reads one message and forwards rpc_request to bot WS."""
        bot_ws = AsyncMock()
        bridge._bots["tok-1"] = bot_ws  # inject directly, skip register_bot

        rpc_req = {"jsonrpc": "2.0", "id": "req_1", "method": "session/prompt", "params": {}}
        payload = json.dumps({"rpc_request": rpc_req})
        mock_queue.consume.side_effect = [
            ("1-0", payload),
            asyncio.CancelledError(),
        ]

        await bridge._poll_bot_inbox("tok-1")

        bot_ws.send_json.assert_awaited_once_with(rpc_req)
        mock_queue.ack.assert_awaited_once_with("bridge:bot_inbox:tok-1", "bot", "1-0")

    async def test_skips_when_inbox_empty(self, bridge, mock_queue):
        """When inbox is empty, consume returns None and loop continues."""
        bridge._bots["tok-1"] = AsyncMock()
        mock_queue.consume.side_effect = [None, asyncio.CancelledError()]

        await bridge._poll_bot_inbox("tok-1")

        bridge._bots["tok-1"].send_json.assert_not_awaited()
        mock_queue.ack.assert_not_awaited()


class TestUnregisterBot:
    async def test_unregister_cleans_redis_and_inboxes(self, bridge, mock_redis, mock_queue):
        ws = AsyncMock()
        mock_redis.zadd.return_value = 1
        await bridge.register_bot("tok-1", ws)

        await bridge.unregister_bot("tok-1")
        assert "tok-1" not in bridge._bots
        mock_redis.zrem.assert_awaited_with("bridge:bot_alive", "tok-1")
        mock_queue.delete_queue.assert_awaited_with("bridge:bot_inbox:tok-1")


class TestCleanupExpiredBots:
    async def test_cleanup_removes_expired_bots(self, bridge, mock_redis, mock_queue):
        """Expired bots' inboxes are deleted and entries removed from ZSET."""
        mock_redis.zrangebyscore.return_value = ["tok-dead"]

        async def _scan_iter(**kwargs):
            yield "bridge:chat_inbox:tok-dead:sid-1"
        mock_redis.scan_iter = _scan_iter

        now = time.time()
        await bridge._cleanup_expired_bots(now)

        mock_queue.delete_queue.assert_awaited_with("bridge:bot_inbox:tok-dead")
        mock_redis.delete.assert_awaited_with("bridge:chat_inbox:tok-dead:sid-1")
        mock_redis.zremrangebyscore.assert_awaited_once()

    async def test_cleanup_noop_when_no_expired(self, bridge, mock_redis, mock_queue):
        mock_redis.zrangebyscore.return_value = []
        await bridge._cleanup_expired_bots(time.time())
        mock_queue.delete_queue.assert_not_awaited()
        mock_redis.zremrangebyscore.assert_not_awaited()


class TestHeartbeat:
    async def test_heartbeat_refreshes_local_bots(self, bridge, mock_redis):
        """Heartbeat ZADD refreshes scores for locally connected bots."""
        bridge._bots["tok-local"] = AsyncMock()
        mock_redis.set.return_value = False  # don't acquire lock

        # Let one iteration run then stop
        original_sleep = asyncio.sleep

        async def _stop_after_one(_):
            bridge._shutting_down = True

        with patch("asyncio.sleep", side_effect=_stop_after_one):
            await bridge._run_heartbeat()

        mock_redis.zadd.assert_awaited_once()
        call_args = mock_redis.zadd.call_args
        assert call_args[0][0] == "bridge:bot_alive"
        assert "tok-local" in call_args[0][1]
