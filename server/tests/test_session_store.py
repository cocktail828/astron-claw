"""Tests for services/session_store.py — SessionStore with mock MySQL + Redis."""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.session_store import SessionStore, _SESSIONS_PREFIX


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_mock_db():
    """Create a mock async session with execute/commit/add/delete stubs."""
    db = AsyncMock()
    db.add = MagicMock()
    db.delete = AsyncMock()
    db.commit = AsyncMock()
    db.execute = AsyncMock()
    return db


def _make_session_factory(db):
    """Wrap a mock db session in a factory that yields it via async context manager."""
    factory = MagicMock()
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=db)
    ctx.__aexit__ = AsyncMock(return_value=False)
    factory.return_value = ctx
    return factory


def _scalar_result(value):
    """Create a mock result whose .scalar() returns `value`."""
    result = MagicMock()
    result.scalar.return_value = value
    result.scalar_one_or_none.return_value = value
    return result


def _scalars_result(values):
    """Create a mock result whose .scalars().all() returns `values`."""
    result = MagicMock()
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = values
    result.scalars.return_value = scalars_mock
    return result


class _FakeRow:
    """Minimal stand-in for an ORM row."""
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestCreateSession:
    async def test_create_session_writes_mysql_and_redis(self, mock_redis):
        db = _make_mock_db()
        factory = _make_session_factory(db)
        store = SessionStore(factory, mock_redis)

        db.execute.side_effect = [_scalar_result(0)]

        num = await store.create_session("tok-1", "sid-aaa")
        assert num == 1

        # MySQL writes
        assert db.add.call_count == 1  # ChatSession only
        db.commit.assert_awaited_once()

        # Redis writes
        mock_redis.rpush.assert_awaited_once()

    async def test_create_session_increments_number(self, mock_redis):
        db = _make_mock_db()
        factory = _make_session_factory(db)
        store = SessionStore(factory, mock_redis)

        db.execute.side_effect = [_scalar_result(3)]
        num = await store.create_session("tok-1", "sid-bbb")
        assert num == 4

    async def test_create_session_redis_failure_non_blocking(self, mock_redis):
        db = _make_mock_db()
        factory = _make_session_factory(db)
        store = SessionStore(factory, mock_redis)

        db.execute.side_effect = [_scalar_result(0)]
        mock_redis.rpush.side_effect = Exception("Redis down")

        num = await store.create_session("tok-1", "sid-ddd")
        assert num == 1  # Still succeeds


class TestGetSession:
    async def test_found(self, mock_redis):
        db = _make_mock_db()
        factory = _make_session_factory(db)
        store = SessionStore(factory, mock_redis)

        row = MagicMock()
        row.session_id = "sid-1"
        row.session_number = 3
        result = MagicMock()
        result.one_or_none.return_value = row
        db.execute.return_value = result

        match = await store.get_session("tok-1", "sid-1")
        assert match == ("sid-1", 3)

    async def test_not_found(self, mock_redis):
        db = _make_mock_db()
        factory = _make_session_factory(db)
        store = SessionStore(factory, mock_redis)

        result = MagicMock()
        result.one_or_none.return_value = None
        db.execute.return_value = result

        match = await store.get_session("tok-1", "nonexistent")
        assert match is None


class TestGetSessions:
    async def test_cache_hit(self, mock_redis):
        db = _make_mock_db()
        factory = _make_session_factory(db)
        store = SessionStore(factory, mock_redis)

        mock_redis.lrange.return_value = ["sid-1", "sid-2"]

        sessions = await store.get_sessions("tok-1")
        assert sessions == [("sid-1", 1), ("sid-2", 2)]
        db.execute.assert_not_awaited()

    async def test_cache_miss_mysql_hit(self, mock_redis):
        db = _make_mock_db()
        factory = _make_session_factory(db)
        store = SessionStore(factory, mock_redis)

        mock_redis.lrange.return_value = []  # Cache miss

        rows = [
            _FakeRow(session_id="sid-1", session_number=1),
            _FakeRow(session_id="sid-2", session_number=2),
        ]

        db.execute.side_effect = [
            _scalars_result(rows),  # get_sessions: sessions query
        ]

        pipe = AsyncMock()
        mock_redis.pipeline.return_value = pipe

        sessions = await store.get_sessions("tok-1")
        assert len(sessions) == 2
        assert sessions[0] == ("sid-1", 1)
        assert sessions[1] == ("sid-2", 2)

    async def test_cache_miss_mysql_empty(self, mock_redis):
        db = _make_mock_db()
        factory = _make_session_factory(db)
        store = SessionStore(factory, mock_redis)

        mock_redis.lrange.return_value = []

        db.execute.side_effect = [_scalars_result([])]

        sessions = await store.get_sessions("tok-1")
        assert sessions == []


class TestRemoveSessions:
    async def test_remove_cleans_mysql_and_redis(self, mock_redis):
        db = _make_mock_db()
        factory = _make_session_factory(db)
        store = SessionStore(factory, mock_redis)

        await store.remove_sessions("tok-1")

        assert db.execute.await_count == 1  # One DELETE statement
        db.commit.assert_awaited_once()
        mock_redis.delete.assert_awaited_once()  # One Redis DELETE


class TestCleanupOldSessions:
    async def test_cleanup_removes_old_sessions(self, mock_redis):
        db = _make_mock_db()
        factory = _make_session_factory(db)
        store = SessionStore(factory, mock_redis)

        old_rows = [
            _FakeRow(token="tok-1", session_id="old-sid-1"),
            _FakeRow(token="tok-1", session_id="old-sid-2"),
        ]

        db.execute.side_effect = [
            MagicMock(all=MagicMock(return_value=old_rows)),  # Select old sessions
            None,                                              # DELETE old sessions
        ]

        result = await store.cleanup_old_sessions(86400)
        assert result == 2
        db.commit.assert_awaited_once()
        # Redis cache invalidated for affected token
        mock_redis.delete.assert_awaited_once()

    async def test_cleanup_nothing_to_remove(self, mock_redis):
        db = _make_mock_db()
        factory = _make_session_factory(db)
        store = SessionStore(factory, mock_redis)

        db.execute.side_effect = [MagicMock(all=MagicMock(return_value=[]))]

        result = await store.cleanup_old_sessions(86400)
        assert result == 0
