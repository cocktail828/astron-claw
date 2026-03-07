"""Tests for services/queue.py — RedisStreamQueue (mock Redis)."""

import pytest
from unittest.mock import AsyncMock

from services.queue import RedisStreamQueue, create_queue


@pytest.fixture()
def redis():
    return AsyncMock()


@pytest.fixture()
def queue(redis):
    return RedisStreamQueue(redis, max_stream_len=500)


class TestPublish:
    async def test_xadd_called(self, queue, redis):
        redis.xadd.return_value = "1709827200000-0"
        entry_id = await queue.publish("my:stream", '{"hello":"world"}')
        assert entry_id == "1709827200000-0"
        redis.xadd.assert_awaited_once_with(
            "my:stream",
            {"data": '{"hello":"world"}'},
            maxlen=500,
            approximate=True,
        )


class TestConsume:
    async def test_returns_message(self, queue, redis):
        redis.xreadgroup.return_value = [
            ["my:stream", [("1-0", {"data": '{"msg":"hi"}'})]]
        ]
        result = await queue.consume("my:stream", "grp", "c1", block_ms=3000)
        assert result == ("1-0", '{"msg":"hi"}')
        redis.xreadgroup.assert_awaited_once_with(
            groupname="grp",
            consumername="c1",
            streams={"my:stream": ">"},
            count=1,
            block=3000,
        )

    async def test_returns_none_on_timeout(self, queue, redis):
        redis.xreadgroup.return_value = None
        result = await queue.consume("my:stream", "grp", "c1")
        assert result is None

    async def test_returns_none_on_empty_entries(self, queue, redis):
        redis.xreadgroup.return_value = [["my:stream", []]]
        result = await queue.consume("my:stream", "grp", "c1")
        assert result is None

    async def test_nogroup_auto_creates(self, queue, redis):
        """When NOGROUP error occurs, ensure_group is called and None is returned."""
        from redis.exceptions import ResponseError
        redis.xreadgroup.side_effect = ResponseError(
            "NOGROUP No such key 'my:stream' or consumer group 'grp'"
        )
        redis.xgroup_create = AsyncMock()

        result = await queue.consume("my:stream", "grp", "c1")
        assert result is None
        redis.xgroup_create.assert_awaited_once_with(
            "my:stream", "grp", id="$", mkstream=True,
        )

    async def test_reraises_non_nogroup_error(self, queue, redis):
        redis.xreadgroup.side_effect = RuntimeError("Connection lost")
        with pytest.raises(RuntimeError, match="Connection lost"):
            await queue.consume("my:stream", "grp", "c1")


class TestAck:
    async def test_xack_called(self, queue, redis):
        await queue.ack("my:stream", "grp", "1-0")
        redis.xack.assert_awaited_once_with("my:stream", "grp", "1-0")


class TestDeleteQueue:
    async def test_delete_called(self, queue, redis):
        await queue.delete_queue("my:stream")
        redis.delete.assert_awaited_once_with("my:stream")


class TestPurge:
    async def test_xtrim_called(self, queue, redis):
        await queue.purge("my:stream")
        redis.xtrim.assert_awaited_once_with("my:stream", maxlen=0)


class TestEnsureGroup:
    async def test_creates_group(self, queue, redis):
        redis.xgroup_create = AsyncMock()
        await queue.ensure_group("my:stream", "grp")
        redis.xgroup_create.assert_awaited_once_with(
            "my:stream", "grp", id="$", mkstream=True,
        )

    async def test_ignores_busygroup(self, queue, redis):
        """BUSYGROUP means the group already exists — should be silently ignored."""
        from redis.exceptions import ResponseError
        redis.xgroup_create = AsyncMock(
            side_effect=ResponseError("BUSYGROUP Consumer Group name already exists")
        )
        await queue.ensure_group("my:stream", "grp")  # should not raise

    async def test_reraises_other_errors(self, queue, redis):
        redis.xgroup_create = AsyncMock(side_effect=RuntimeError("Boom"))
        with pytest.raises(RuntimeError, match="Boom"):
            await queue.ensure_group("my:stream", "grp")


class TestCreateQueueFactory:
    def test_redis_stream(self):
        mock_redis = AsyncMock()
        q = create_queue("redis_stream", mock_redis, max_stream_len=2000)
        assert isinstance(q, RedisStreamQueue)
        assert q._max_len == 2000

    def test_unsupported_type(self):
        with pytest.raises(ValueError, match="Unsupported queue type"):
            create_queue("kafka", AsyncMock())
