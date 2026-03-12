from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import TYPE_CHECKING, Optional
from urllib.parse import urlparse, unquote, quote, urlunparse

from redis.asyncio import Redis

from infra.errors import Err
from infra.log import logger

if TYPE_CHECKING:
    from fastapi import WebSocket

    from services.queue import MessageQueue
    from services.session_store import SessionStore

_BOT_ALIVE_KEY = "bridge:bot_alive"            # ZSET: score=timestamp, member=token
_BOT_INBOX_PREFIX = "bridge:bot_inbox:"        # STREAM per token: messages TO bot
CHAT_INBOX_PREFIX = "bridge:chat_inbox:"       # STREAM per chat: messages TO chat (shared with sse.py)
_CLEANUP_LOCK_KEY = "bridge:cleanup_lock"      # distributed lock for stale cleanup

_BOT_TTL = 30            # seconds before a bot is considered offline
_HEARTBEAT_INTERVAL = 10 # how often each worker refreshes its heartbeat
_BOT_SILENT_TTL = 45     # seconds: if no data received from bot WS, consider it dead
_CONSUME_BLOCK_MS = 5000 # XREADGROUP block timeout (milliseconds)


class ConnectionBridge:
    """Manages the mapping between bot connections and chat clients.

    Each token can have one bot WebSocket.
    Messages flow: chat (SSE) -> server (JSON-RPC) -> bot -> server -> chat (SSE).
    Session data is persisted in MySQL via SessionStore, with Redis as a
    write-through cache. Bot WebSocket refs stay in-memory.

    Multi-worker safe: cross-worker message routing uses per-token Redis
    Streams (XADD / XREADGROUP), compatible with both standalone and
    cluster modes.

    Bot liveness is tracked via a single ZSET (``bridge:bot_alive``) where
    each member is a token and the score is the last heartbeat timestamp.
    A distributed lock (``bridge:cleanup_lock``) ensures only one worker
    per cycle performs stale-entry cleanup.
    """

    def __init__(
        self,
        redis: Redis,
        session_store: SessionStore,
        queue: MessageQueue,
    ):
        self._worker_id = uuid.uuid4().hex[:12]
        # token -> bot WebSocket (process-local)
        self._bots: dict[str, WebSocket] = {}
        # token -> last time we received any data from this bot WS
        self._bot_last_seen: dict[str, float] = {}
        # Redis client for cross-worker state
        self._redis = redis
        # Session persistence layer (MySQL + Redis cache)
        self._session_store = session_store
        # Message queue abstraction (Redis Streams)
        self._queue = queue
        # Per-connection consume tasks: task_key -> asyncio.Task
        self._poll_tasks: dict[str, asyncio.Task] = {}
        # Background tasks
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._shutting_down = False

    async def start(self) -> None:
        """Start the worker heartbeat."""
        self._heartbeat_task = asyncio.create_task(self._run_heartbeat())
        logger.info("Bridge worker {} started", self._worker_id)

    async def _run_heartbeat(self) -> None:
        """Periodically refresh bot heartbeats and compete for cleanup duty."""
        while not self._shutting_down:
            try:
                now = time.time()
                # Detect half-open WebSocket connections: if we haven't
                # received any data from a bot within _BOT_SILENT_TTL,
                # treat its connection as dead and forcibly unregister it.
                stale_tokens = [
                    (token, last_seen)
                    for token, last_seen in self._bot_last_seen.items()
                    if (now - last_seen) >= _BOT_SILENT_TTL
                ]
                for token, last_seen in stale_tokens:
                    logger.warning(
                        "Bot silent for {}s, force-closing (worker={}, token={}...)",
                        int(now - last_seen),
                        self._worker_id, token[:10],
                    )
                    ws = self._bots.get(token)
                    if ws:
                        try:
                            await ws.close(code=4004, reason="Liveness timeout")
                        except Exception:
                            pass
                    await self.unregister_bot(token)

                # All workers: refresh heartbeat only for still-alive bots
                if self._bots:
                    mapping = {token: now for token in self._bots}
                    await self._redis.zadd(_BOT_ALIVE_KEY, mapping)

                # Compete for cleanup lock — only winner runs stale cleanup
                acquired = await self._redis.set(
                    _CLEANUP_LOCK_KEY, self._worker_id,
                    nx=True, ex=_HEARTBEAT_INTERVAL,
                )
                if acquired:
                    await self._cleanup_expired_bots(now)
            except Exception:
                if not self._shutting_down:
                    logger.exception("Heartbeat failed (worker={})", self._worker_id)
            await asyncio.sleep(_HEARTBEAT_INTERVAL)

    async def _cleanup_expired_bots(self, now: float) -> None:
        """Remove expired bot entries and their associated inboxes.

        Called only by the worker that acquired the cleanup lock.
        """
        cutoff = now - _BOT_TTL
        expired = await self._redis.zrangebyscore(_BOT_ALIVE_KEY, "-inf", cutoff)
        if not expired:
            return

        for tok in expired:
            tok_str = tok if isinstance(tok, str) else tok.decode()
            await self._queue.delete_queue(f"{_BOT_INBOX_PREFIX}{tok_str}")
            await self._cleanup_chat_inboxes(tok_str)

        await self._redis.zremrangebyscore(_BOT_ALIVE_KEY, "-inf", cutoff)
        logger.info("Cleanup: removed {} expired bot(s)", len(expired))

    async def _cleanup_chat_inboxes(self, token: str) -> None:
        """Delete all chat inbox streams for the given token."""
        pattern = f"{CHAT_INBOX_PREFIX}{token}:*"
        batch: list[str] = []
        async for key in self._redis.scan_iter(match=pattern, count=100):
            batch.append(key)
        if batch:
            await self._redis.delete(*batch)

    # ── Bot registration (multi-worker safe) ─────────────────────────────────

    async def register_bot(self, token: str, ws: WebSocket) -> bool:
        """Register a bot connection. Returns False if a live bot is already connected.

        Uses ZADD NX for atomic registration to prevent race conditions
        between workers competing to register the same token.
        """
        if token in self._bots:
            return False

        # Atomic: only succeeds if token is NOT already in the ZSET
        added = await self._redis.zadd(
            _BOT_ALIVE_KEY, {token: time.time()}, nx=True,
        )
        if not added:
            # Another worker registered first, or stale heartbeat remains
            score = await self._redis.zscore(_BOT_ALIVE_KEY, token)
            if score is not None and (time.time() - score) < _BOT_TTL:
                return False
            # Stale heartbeat expired — overwrite only if our timestamp is newer
            await self._redis.zadd(_BOT_ALIVE_KEY, {token: time.time()}, gt=True)

        self._bots[token] = ws
        self._bot_last_seen[token] = time.time()
        # Ensure consumer group exists and start consuming bot inbox
        inbox = f"{_BOT_INBOX_PREFIX}{token}"
        await self._queue.ensure_group(inbox, "bot")
        task_key = f"bot:{token}"
        self._poll_tasks[task_key] = asyncio.create_task(self._poll_bot_inbox(token))
        logger.info("Bot registered on worker {} (token={}...)", self._worker_id, token[:10])
        return True

    async def unregister_bot(self, token: str, ws: WebSocket | None = None) -> None:
        """Remove bot from local dict + clean up Redis and inboxes.

        When *ws* is provided, the cleanup is skipped if ``_bots[token]`` no
        longer points to that same WebSocket instance — this prevents a stale
        finally-block from accidentally destroying a newer connection that has
        already re-registered under the same token.
        """
        current_ws = self._bots.get(token)
        if ws is not None and current_ws is not ws:
            # A newer connection already replaced this one; nothing to clean.
            return
        self._bots.pop(token, None)
        self._bot_last_seen.pop(token, None)
        # Stop bot inbox consuming
        task_key = f"bot:{token}"
        task = self._poll_tasks.pop(task_key, None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        # Notify BEFORE cleanup so the log reflects accurate state
        self.notify_bot_disconnected(token)
        await self._redis.zrem(_BOT_ALIVE_KEY, token)
        await self._queue.delete_queue(f"{_BOT_INBOX_PREFIX}{token}")
        await self._cleanup_chat_inboxes(token)
        logger.info("Bot unregistered (worker={}, token={}...)", self._worker_id, token[:10])

    async def remove_bot_sessions(self, token: str) -> None:
        """Destroy session data for a token. Called only on admin token delete."""
        await self._session_store.remove_sessions(token)

        # Disconnect local bot if on this worker
        if token in self._bots:
            bot_ws = self._bots[token]
            try:
                await bot_ws.close(code=4003, reason="Token deleted")
            except Exception:
                logger.warning("Failed to close bot WebSocket during token removal (token={}...)", token[:10])
            await self.unregister_bot(token)
        else:
            # Bot may be on a remote worker — push disconnect command to inbox
            inbox = f"{_BOT_INBOX_PREFIX}{token}"
            await self._queue.publish(inbox, json.dumps({"_disconnect": True}))
            # Clean Redis keys (unregister_bot already handles the local case)
            await self._redis.zrem(_BOT_ALIVE_KEY, token)
            await self._queue.delete_queue(f"{_BOT_INBOX_PREFIX}{token}")
            await self._cleanup_chat_inboxes(token)
        logger.info("Bot sessions fully removed (token={}...)", token[:10])

    # ── Queries (read from Redis for cluster-wide view) ───────────────────────

    async def is_bot_connected(self, token: str) -> bool:
        """Return True only if a bot's heartbeat is fresh (within _BOT_TTL)."""
        score = await self._redis.zscore(_BOT_ALIVE_KEY, token)
        if score is None:
            return False
        return (time.time() - score) < _BOT_TTL

    async def get_connections_summary(self) -> dict[str, dict]:
        """Return per-token bot online status (cluster-wide)."""
        cutoff = time.time() - _BOT_TTL
        alive_tokens = await self._redis.zrangebyscore(
            _BOT_ALIVE_KEY, cutoff, "+inf",
        )
        return {
            (t if isinstance(t, str) else t.decode()): {"bot_online": True}
            for t in alive_tokens
        }

    # ── Session management (delegated to SessionStore) ─────────────────────

    async def create_session(self, token: str) -> tuple[str, int]:
        """Create a new session, persist to MySQL, cache in Redis."""
        session_id = str(uuid.uuid4())
        session_number = await self._session_store.create_session(token, session_id)
        logger.info("Session created: {} (token={}...)", session_id[:8], token[:10])
        return session_id, session_number

    async def reset_session(self, token: str) -> tuple[str, int]:
        return await self.create_session(token)

    async def get_session(self, token: str, session_id: str) -> Optional[tuple[str, int]]:
        return await self._session_store.get_session(token, session_id)

    async def get_sessions(self, token: str) -> list[tuple[str, int]]:
        return await self._session_store.get_sessions(token)

    async def cleanup_old_sessions(self, max_age_days: float) -> int:
        return await self._session_store.cleanup_old_sessions(max_age_days * 86400)

    # ── Message routing (cross-worker via per-token inbox) ─────────────────

    async def send_to_bot(
        self,
        token: str,
        user_message: str,
        media_urls: list[str] | None = None,
        session_id: str = "",
    ) -> Optional[str]:
        """Create a JSON-RPC request and send it to the bot.

        session_id must be provided by the caller (resolved in the SSE layer).
        """
        if not session_id:
            logger.error("send_to_bot called without session_id (token={}...)", token[:10])
            return None

        request_id = f"req_{uuid.uuid4().hex[:12]}"

        # Build prompt content
        content_items = []

        if user_message:
            content_items.append({"type": "text", "content": user_message})

        for url in (media_urls or []):
            encoded_url = _ensure_encoded_url(url)
            content_items.append({"type": "url", "content": encoded_url})

        if not content_items:
            logger.error("send_to_bot called with empty content (token={}...)", token[:10])
            return None

        rpc_request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "session/prompt",
            "params": {
                "sessionId": session_id,
                "prompt": {
                    "content": content_items,
                },
            },
        }

        # Always route via inbox (works for both local and remote workers)
        try:
            inbox = f"{_BOT_INBOX_PREFIX}{token}"
            await self._queue.publish(inbox, json.dumps({"rpc_request": rpc_request}))
        except Exception:
            logger.exception("Failed to push to bot inbox (token={}...)", token[:10])
            return None
        logger.info("Sent to bot (inbox): req={} media={} (token={}...)", request_id, len(media_urls or []), token[:10])
        return request_id

    async def handle_bot_message(self, token: str, raw: str) -> None:
        """Parse a JSON-RPC message from the bot and forward to chat clients."""
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Invalid JSON from bot (token={}...)", token[:10])
            return

        if msg.get("type") == "ping":
            return

        method = msg.get("method", "")
        params = msg.get("params", {})

        if method:
            chat_event = _translate_bot_event(method, params)
            # Notifications carry sessionId in params; route to that session
            session_id = params.get("sessionId") if params else None
            if not session_id:
                logger.warning("Bot notification missing sessionId: method={} (token={}...)", method, token[:10])
            # Chunk events are high-frequency — use DEBUG to avoid flooding INFO
            if chat_event and chat_event.get("type") in ("chunk", "thinking"):
                logger.debug("Bot event: method={} type={} session={} (token={}...)", method, chat_event["type"], session_id[:8] if session_id else "?", token[:10])
            else:
                logger.info("Bot event: method={} session={} (token={}...)", method, session_id[:8] if session_id else "?", token[:10])
            if chat_event:
                if session_id:
                    await self._send_to_session(token, session_id, chat_event)
            else:
                logger.warning("Bot event dropped: method={} untranslatable (token={}...)", method, token[:10])

        if "id" in msg and "result" in msg:
            session_id = msg.get("sessionId")
            logger.info("Bot result: req={} session={} (token={}...)", msg["id"], session_id[:8] if session_id else "?", token[:10])

        if "id" in msg and "error" in msg:
            session_id = msg.get("sessionId")
            error_msg = msg["error"].get("message", Err.BOT_UNKNOWN_ERROR.message)
            logger.error("Bot JSON-RPC error: {} (token={}...)", error_msg, token[:10])
            error_event = {"type": "error", "content": error_msg}
            if session_id:
                await self._send_to_session(token, session_id, error_event)
            else:
                logger.warning("Cannot route RPC error: missing sessionId in response (token={}...)", token[:10])

    # ── Bot status logging ──────────────────────────────────────────────────

    def notify_bot_connected(self, token: str) -> None:
        logger.info("Bot status -> connected (token={}...)", token[:10])

    def notify_bot_disconnected(self, token: str) -> None:
        logger.info("Bot status -> disconnected (token={}...)", token[:10])

    def mark_bot_seen(self, token: str) -> None:
        """Update the last-seen timestamp for a locally connected bot."""
        if token in self._bots:
            self._bot_last_seen[token] = time.time()

    # ── Broadcasting ──────────────────────────────────────────────────────────

    async def _send_to_session(self, token: str, session_id: str, event: dict) -> None:
        """Send event to a specific session's chat inbox via Redis Stream.

        Skips the write when the inbox stream does not exist, which means
        no SSE consumer is currently listening.
        """
        try:
            inbox = f"{CHAT_INBOX_PREFIX}{token}:{session_id}"
            if not await self._redis.exists(inbox):
                logger.debug("No active SSE consumer, skipping event: type={} session={} (token={}...)", event.get("type"), session_id[:8], token[:10])
                return
            await self._queue.publish(inbox, json.dumps(event))
            logger.debug("Event pushed to session inbox: type={} session={} (token={}...)", event.get("type"), session_id[:8], token[:10])
        except Exception:
            if not self._shutting_down:
                logger.exception("Failed to send to session inbox (token={}... session={}...)", token[:10], session_id[:8])

    # ── Per-connection inbox consuming ───────────────────────────────────────

    async def _poll_bot_inbox(self, token: str) -> None:
        """Consume bot_inbox:{token} via XREADGROUP and forward to the local bot WS."""
        inbox = f"{_BOT_INBOX_PREFIX}{token}"
        while not self._shutting_down:
            try:
                result = await self._queue.consume(
                    inbox, group="bot", consumer="bot",
                    block_ms=_CONSUME_BLOCK_MS,
                )
                if result is None:
                    # XREADGROUP BLOCK normally waits at Redis level, but if the
                    # call returns instantly (e.g. repeated NOGROUP recovery),
                    # sleep 1s to prevent a tight CPU-burning loop.
                    await asyncio.sleep(1)
                    continue
                msg_id, raw = result
                data = json.loads(raw)
                await self._queue.ack(inbox, "bot", msg_id)
                await self._queue.delete_message(inbox, msg_id)
                # Handle disconnect command from admin token delete
                if data.get("_disconnect"):
                    bot_ws = self._bots.get(token)
                    if bot_ws:
                        try:
                            await bot_ws.close(code=4003, reason="Token deleted")
                        except Exception:
                            logger.warning("Failed to close bot WebSocket on disconnect command (token={}...)", token[:10])
                    logger.info("Inbox: received disconnect for bot (token={}...)", token[:10])
                    break
                bot_ws = self._bots.get(token)
                if bot_ws:
                    await bot_ws.send_json(data["rpc_request"])
                    logger.info("Inbox: forwarded to local bot (token={}...)", token[:10])
                else:
                    logger.warning("Inbox: bot WS gone, message dropped (token={}...)", token[:10])
            except asyncio.CancelledError:
                break
            except Exception:
                if not self._shutting_down:
                    logger.exception("Bot inbox consume error (token={}...)", token[:10])
                    await asyncio.sleep(1)

    # ── Graceful shutdown ─────────────────────────────────────────────────────

    async def shutdown(self) -> None:
        """Graceful shutdown: close all connections, clean up Redis."""
        self._shutting_down = True
        logger.info("Bridge worker {} shutting down...", self._worker_id)

        # Close bot connections and clean Redis
        for token, ws in list(self._bots.items()):
            try:
                await ws.close(code=4000, reason="Server restarting")
            except Exception:
                logger.debug("WebSocket close error during shutdown (ignored)")
            await self._redis.zrem(_BOT_ALIVE_KEY, token)
            await self._queue.delete_queue(f"{_BOT_INBOX_PREFIX}{token}")
            await self._cleanup_chat_inboxes(token)
        self._bots.clear()
        self._bot_last_seen.clear()

        # Cancel all polling tasks
        for task in self._poll_tasks.values():
            task.cancel()
        for task in self._poll_tasks.values():
            try:
                await task
            except asyncio.CancelledError:
                logger.debug("Poll task cancelled during shutdown")
        self._poll_tasks.clear()

        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                logger.debug("Heartbeat task cancelled during shutdown")

        logger.info("Bridge worker {} shutdown complete", self._worker_id)


def _ensure_encoded_url(url: str) -> str:
    """Ensure URL path is properly percent-encoded (handles Unicode chars)."""
    parsed = urlparse(url)
    decoded_path = unquote(parsed.path)
    encoded_path = quote(decoded_path, safe='/')
    return urlunparse((
        parsed.scheme, parsed.netloc, encoded_path,
        parsed.params, parsed.query, parsed.fragment,
    ))


def _translate_bot_event(method: str, params: dict) -> Optional[dict]:
    """Convert a bot JSON-RPC notification to a simplified chat event."""
    if method == "session/update":
        update = params.get("update", {})
        update_type = update.get("sessionUpdate", "")
        content = update.get("content", {})

        if update_type == "agent_message_chunk":
            return {"type": "chunk", "content": content.get("text", "")}
        if update_type == "agent_message_final":
            return {"type": "done", "content": content.get("text", "")}
        if update_type == "tool_result":
            result_text = update.get("content", "")
            if not isinstance(result_text, str):
                if isinstance(result_text, dict):
                    result_text = result_text.get("text", "")
                else:
                    result_text = json.dumps(result_text) if result_text else ""
            title = update.get("title", "tool")
            status = update.get("status", "completed")
            return {"type": "tool_result", "name": title, "status": status, "content": result_text}
        if update_type == "agent_thought_chunk":
            return {"type": "thinking", "content": content.get("text", "")}
        if update_type == "tool_call":
            title = update.get("title", "tool")
            input_text = update.get("content", "")
            if not isinstance(input_text, str):
                input_text = json.dumps(input_text) if input_text else ""
            return {"type": "tool_call", "name": title, "input": input_text}

        if update_type == "agent_media":
            media = content.get("media", {})
            download_url = media.get("downloadUrl", "")
            if not download_url:
                logger.warning("agent_media event missing downloadUrl")
                return None
            data: dict = {"type": "url", "content": download_url}
            caption = content.get("text", "")
            if caption:
                data["caption"] = caption
            return {"type": "media", "data": data}

        if isinstance(content, dict) and "text" in content:
            logger.debug("Bot event fallback to chunk: sessionUpdate={} (unknown type)", update_type)
            return {"type": "chunk", "content": content["text"]}
        logger.warning("Bot event untranslatable: sessionUpdate={}", update_type)
        return None

    return None

