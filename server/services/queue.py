"""Message queue abstraction and Redis Streams implementation.

Provides a backend-agnostic ``MessageQueue`` ABC so the bridge and SSE
layers never touch queue internals directly.  The default (and currently
only) implementation is ``RedisStreamQueue`` which wraps Redis Streams
commands (XADD / XREADGROUP / XACK / XTRIM).

A ``create_queue()`` factory selects the concrete implementation based
on configuration so callers only depend on the abstract interface.
"""

from abc import ABC, abstractmethod
from typing import Optional

from redis.asyncio import Redis
from redis.asyncio.cluster import RedisCluster

from infra.log import logger


# ---------------------------------------------------------------------------
# Abstract base class
# ---------------------------------------------------------------------------

class MessageQueue(ABC):
    """Backend-agnostic message queue interface."""

    @abstractmethod
    async def publish(self, queue_name: str, message: str) -> str:
        """Append *message* to *queue_name*.

        Returns:
            A backend-specific message ID (e.g. a Redis Stream entry ID).
        """
        ...

    @abstractmethod
    async def consume(
        self,
        queue_name: str,
        group: str,
        consumer: str,
        block_ms: int = 5000,
    ) -> Optional[tuple[str, str]]:
        """Block-read one message from *queue_name*.

        Returns:
            ``(message_id, payload)`` or ``None`` when *block_ms* elapses
            with no new messages.
        """
        ...

    @abstractmethod
    async def ack(self, queue_name: str, group: str, message_id: str) -> None:
        """Acknowledge successful processing of *message_id*."""
        ...

    @abstractmethod
    async def delete_message(self, queue_name: str, message_id: str) -> None:
        """Remove *message_id* from the stream to reclaim memory."""
        ...

    @abstractmethod
    async def delete_queue(self, queue_name: str) -> None:
        """Delete the queue and all its data."""
        ...

    @abstractmethod
    async def purge(self, queue_name: str) -> None:
        """Remove all messages but keep the queue structure."""
        ...

    @abstractmethod
    async def ensure_group(self, queue_name: str, group: str) -> None:
        """Create the consumer group if it does not already exist.

        New groups start at offset ``$`` (only future messages).
        """
        ...


# ---------------------------------------------------------------------------
# Redis Streams implementation
# ---------------------------------------------------------------------------

class RedisStreamQueue(MessageQueue):
    """``MessageQueue`` backed by Redis Streams.

    Each *queue_name* maps to a single Redis Stream key.  Consumer groups
    are created lazily via :meth:`ensure_group` which auto-creates the
    underlying key if absent.

    Compatible with both Redis standalone and Redis Cluster (every
    operation touches a single key).
    """

    def __init__(self, redis: Redis, *, max_stream_len: int = 1000) -> None:
        self._redis = redis
        self._max_len = max_stream_len

    # -- publish -------------------------------------------------------------

    async def publish(self, queue_name: str, message: str) -> str:
        entry_id: str = await self._redis.xadd(
            queue_name,
            {"data": message},
            maxlen=self._max_len,
            approximate=True,
        )
        logger.debug("Queue publish: stream={} msg_id={}", queue_name, entry_id)
        return entry_id

    # -- consume -------------------------------------------------------------

    async def consume(
        self,
        queue_name: str,
        group: str,
        consumer: str,
        block_ms: int = 5000,
    ) -> Optional[tuple[str, str]]:
        try:
            result = await self._redis.xreadgroup(
                groupname=group,
                consumername=consumer,
                streams={queue_name: ">"},
                count=1,
                block=block_ms,
            )
        except Exception as exc:
            # NOGROUP — group hasn't been created yet (race on first call)
            err_msg = str(exc)
            if "NOGROUP" in err_msg:
                logger.warning("Queue NOGROUP: stream={} group={}, recreated", queue_name, group)
                await self.ensure_group(queue_name, group)
                return None
            raise

        if not result:
            return None

        # result structure: [[stream_name, [(entry_id, {field: value})]]]
        _stream_name, entries = result[0]
        if not entries:
            return None

        entry_id, fields = entries[0]
        logger.debug("Queue consume: stream={} group={} msg_id={}", queue_name, group, entry_id)
        return (entry_id, fields.get("data", ""))

    # -- ack -----------------------------------------------------------------

    async def ack(self, queue_name: str, group: str, message_id: str) -> None:
        await self._redis.xack(queue_name, group, message_id)
        logger.debug("Queue ack: stream={} msg_id={}", queue_name, message_id)

    # -- delete_message ------------------------------------------------------

    async def delete_message(self, queue_name: str, message_id: str) -> None:
        await self._redis.xdel(queue_name, message_id)
        logger.debug("Queue delete_message: stream={} msg_id={}", queue_name, message_id)

    # -- delete_queue --------------------------------------------------------

    async def delete_queue(self, queue_name: str) -> None:
        await self._redis.delete(queue_name)
        logger.debug("Queue delete_queue: stream={}", queue_name)

    # -- purge ---------------------------------------------------------------

    async def purge(self, queue_name: str) -> None:
        await self._redis.xtrim(queue_name, maxlen=0)
        logger.debug("Queue purge: stream={}", queue_name)

    # -- ensure_group --------------------------------------------------------

    async def ensure_group(self, queue_name: str, group: str) -> None:
        try:
            # Manually ensure the stream key exists before creating the
            # consumer group.  This avoids passing ``mkstream=True`` which
            # can cause encoding issues (bytes vs str) on RedisCluster
            # with ``decode_responses=True``.
            if not await self._redis.exists(queue_name):
                entry_id = await self._redis.xadd(queue_name, {"_init": "1"})
                await self._redis.xdel(queue_name, entry_id)

            # RedisCluster._determine_slot cannot resolve the key
            # position for XGROUP subcommands (neither the high-level
            # xgroup_create() nor execute_command with split tokens work).
            # Bypass slot detection entirely by computing the target node
            # from the key ourselves.
            if isinstance(self._redis, RedisCluster):
                node = self._redis.nodes_manager.get_node_from_slot(
                    self._redis.keyslot(queue_name),
                )
                await self._redis.execute_command(
                    "XGROUP", "CREATE", queue_name, group, "$",
                    target_nodes=node,
                )
            else:
                await self._redis.xgroup_create(
                    queue_name, group, id="$",
                )
        except Exception as exc:
            # BUSYGROUP — group already exists, safe to ignore
            if "BUSYGROUP" in str(exc):
                logger.debug("Queue ensure_group: stream={} group={} (already exists)", queue_name, group)
                return
            raise


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_queue(queue_type: str, redis: Redis, **kwargs) -> MessageQueue:
    """Instantiate a :class:`MessageQueue` implementation.

    Args:
        queue_type: ``"redis_stream"`` (only supported value today).
        redis: An initialised async Redis client.
        **kwargs: Forwarded to the concrete constructor
                  (e.g. ``max_stream_len``).
    """
    if queue_type == "redis_stream":
        return RedisStreamQueue(redis, **kwargs)
    raise ValueError(f"Unsupported queue type: {queue_type!r}")
