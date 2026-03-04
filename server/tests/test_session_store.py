"""Tests for services/session_store.py — SessionStore with mock MySQL + Redis."""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.session_store import SessionStore, _SESSIONS_PREFIX, _ACTIVE_PREFIX


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

        # First execute: max(session_number) -> 0
        # Second execute: select active session -> None
        db.execute.side_effect = [_scalar_result(0), _scalar_result(None)]

        num = await store.create_session("tok-1", "sid-aaa")
        assert num == 1

        # MySQL writes
        assert db.add.call_count == 2  # ChatSession + ChatActiveSession
        db.commit.assert_awaited_once()

        # Redis writes
        mock_redis.rpush.assert_awaited_once()
        mock_redis.set.assert_awaited()

    async def test_create_session_increments_number(self, mock_redis):
        db = _make_mock_db()
        factory = _make_session_factory(db)
        store = SessionStore(factory, mock_redis)

        db.execute.side_effect = [_scalar_result(3), _scalar_result(None)]
        num = await store.create_session("tok-1", "sid-bbb")
        assert num == 4

    async def test_create_session_updates_existing_active(self, mock_redis):
        db = _make_mock_db()
        factory = _make_session_factory(db)
        store = SessionStore(factory, mock_redis)

        active_row = _FakeRow(active_session_id="old-sid", updated_at=0.0)
        db.execute.side_effect = [_scalar_result(1), _scalar_result(active_row)]

        num = await store.create_session("tok-1", "sid-ccc")
        assert num == 2
        assert active_row.active_session_id == "sid-ccc"
        # Only ChatSession added (existing active row mutated)
        assert db.add.call_count == 1

    async def test_create_session_redis_failure_non_blocking(self, mock_redis):
        db = _make_mock_db()
        factory = _make_session_factory(db)
        store = SessionStore(factory, mock_redis)

        db.execute.side_effect = [_scalar_result(0), _scalar_result(None)]
        mock_redis.rpush.side_effect = Exception("Redis down")

        num = await store.create_session("tok-1", "sid-ddd")
        assert num == 1  # Still succeeds


class TestGetActiveSession:
    async def test_cache_hit(self, mock_redis):
        db = _make_mock_db()
        factory = _make_session_factory(db)
        store = SessionStore(factory, mock_redis)

        mock_redis.get.return_value = "cached-sid"
        mock_redis.ttl.return_value = 3500  # Has TTL → not a legacy key
        result = await store.get_active_session("tok-1")
        assert result == "cached-sid"
        db.execute.assert_not_awaited()

    async def test_cache_hit_legacy_triggers_migration(self, mock_redis):
        db = _make_mock_db()
        factory = _make_session_factory(db)
        store = SessionStore(factory, mock_redis)

        mock_redis.get.return_value = "cached-sid"
        mock_redis.ttl.return_value = -1  # No TTL → legacy key
        mock_redis.lrange.return_value = ["cached-sid"]
        # _maybe_migrate: MySQL count check
        db.execute.side_effect = [_scalar_result(0)]

        result = await store.get_active_session("tok-1")
        assert result == "cached-sid"
        # Migration should have been triggered
        assert db.add.call_count == 2  # ChatSession + ChatActiveSession
        db.commit.assert_awaited_once()

    async def test_cache_miss_mysql_hit(self, mock_redis):
        db = _make_mock_db()
        factory = _make_session_factory(db)
        store = SessionStore(factory, mock_redis)

        mock_redis.get.return_value = None
        mock_redis.lrange.return_value = []  # No Redis data for lazy migration

        active_row = _FakeRow(active_session_id="db-sid", updated_at=1.0)
        # First execute: select active -> row
        # Lazy migration: lrange returns [] so no migration
        # _repopulate_cache: two more selects
        db.execute.side_effect = [
            _scalar_result(active_row),      # get_active_session MySQL query
            _scalars_result([]),              # _repopulate_cache: sessions
            _scalar_result(active_row),      # _repopulate_cache: active
        ]

        # Mock pipeline for repopulate
        pipe = AsyncMock()
        mock_redis.pipeline.return_value = pipe

        result = await store.get_active_session("tok-1")
        assert result == "db-sid"

    async def test_cache_miss_mysql_miss_no_migration(self, mock_redis):
        db = _make_mock_db()
        factory = _make_session_factory(db)
        store = SessionStore(factory, mock_redis)

        mock_redis.get.return_value = None
        mock_redis.lrange.return_value = []  # No Redis data either

        db.execute.side_effect = [
            _scalar_result(None),   # First MySQL query: no active session
            _scalar_result(None),   # After migration attempt: still none
        ]

        result = await store.get_active_session("tok-1")
        assert result is None


class TestGetSessions:
    async def test_cache_hit(self, mock_redis):
        db = _make_mock_db()
        factory = _make_session_factory(db)
        store = SessionStore(factory, mock_redis)

        mock_redis.lrange.return_value = ["sid-1", "sid-2"]
        mock_redis.get.return_value = "sid-2"
        mock_redis.ttl.return_value = 3500  # Has TTL → not a legacy key

        sessions, active = await store.get_sessions("tok-1")
        assert sessions == [("sid-1", 1), ("sid-2", 2)]
        assert active == "sid-2"
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
        active_row = _FakeRow(active_session_id="sid-2", updated_at=1.0)

        # get_sessions opens multiple `async with self._sf()` blocks:
        # 1. Select sessions (scalars) — cache miss, rows found
        # 2. Select active session (scalar_one_or_none)
        # _repopulate_cache opens another block:
        # 3. Select sessions (scalars) for cache rebuild
        # 4. Select active (scalar_one_or_none) for cache rebuild
        db.execute.side_effect = [
            _scalars_result(rows),           # get_sessions: sessions query
            _scalar_result(active_row),      # get_sessions: active query
            _scalars_result(rows),           # _repopulate_cache: sessions
            _scalar_result(active_row),      # _repopulate_cache: active
        ]

        pipe = AsyncMock()
        mock_redis.pipeline.return_value = pipe

        sessions, active = await store.get_sessions("tok-1")
        assert len(sessions) == 2
        assert sessions[0] == ("sid-1", 1)
        assert active == "sid-2"


class TestSwitchSession:
    async def test_switch_success(self, mock_redis):
        db = _make_mock_db()
        factory = _make_session_factory(db)
        store = SessionStore(factory, mock_redis)

        session_row = _FakeRow(session_id="sid-1", token="tok-1")
        active_row = _FakeRow(active_session_id="old-sid", updated_at=0.0)
        db.execute.side_effect = [
            _scalar_result(session_row),     # Verify existence
            _scalar_result(active_row),      # Get active row
        ]

        result = await store.switch_session("tok-1", "sid-1")
        assert result is True
        assert active_row.active_session_id == "sid-1"
        mock_redis.set.assert_awaited()

    async def test_switch_not_found(self, mock_redis):
        db = _make_mock_db()
        factory = _make_session_factory(db)
        store = SessionStore(factory, mock_redis)

        db.execute.side_effect = [_scalar_result(None)]  # Not found

        result = await store.switch_session("tok-1", "nonexistent")
        assert result is False


class TestRemoveSessions:
    async def test_remove_cleans_mysql_and_redis(self, mock_redis):
        db = _make_mock_db()
        factory = _make_session_factory(db)
        store = SessionStore(factory, mock_redis)

        await store.remove_sessions("tok-1")

        assert db.execute.await_count == 2  # Two DELETE statements
        db.commit.assert_awaited_once()
        assert mock_redis.delete.await_count == 2  # Two Redis DELETEs


class TestLazyMigration:
    async def test_migrate_from_redis_to_mysql(self, mock_redis):
        db = _make_mock_db()
        factory = _make_session_factory(db)
        store = SessionStore(factory, mock_redis)

        # Redis has data
        mock_redis.lrange.return_value = ["sid-1", "sid-2"]
        mock_redis.get.return_value = "sid-2"

        # MySQL has no data
        db.execute.side_effect = [_scalar_result(0)]

        await store._maybe_migrate_from_redis("tok-1")

        # Should add 2 ChatSession rows + 1 ChatActiveSession
        assert db.add.call_count == 3
        db.commit.assert_awaited_once()

    async def test_no_migration_when_mysql_has_data(self, mock_redis):
        db = _make_mock_db()
        factory = _make_session_factory(db)
        store = SessionStore(factory, mock_redis)

        mock_redis.lrange.return_value = ["sid-1"]
        db.execute.side_effect = [_scalar_result(1)]  # MySQL already has data

        await store._maybe_migrate_from_redis("tok-1")

        db.add.assert_not_called()
        db.commit.assert_not_awaited()

    async def test_no_migration_when_redis_empty(self, mock_redis):
        db = _make_mock_db()
        factory = _make_session_factory(db)
        store = SessionStore(factory, mock_redis)

        mock_redis.lrange.return_value = []

        await store._maybe_migrate_from_redis("tok-1")

        db.execute.assert_not_awaited()


class TestCleanupOldSessions:
    async def test_cleanup_removes_old_sessions(self, mock_redis):
        db = _make_mock_db()
        factory = _make_session_factory(db)
        store = SessionStore(factory, mock_redis)

        old_rows = [
            _FakeRow(token="tok-1", session_id="old-sid-1"),
            _FakeRow(token="tok-1", session_id="old-sid-2"),
        ]
        active_row = _FakeRow(active_session_id="old-sid-1", updated_at=0.0)
        newest_row = _FakeRow(session_id="new-sid", session_number=3)

        db.execute.side_effect = [
            MagicMock(all=MagicMock(return_value=old_rows)),  # Select old sessions
            None,                                              # DELETE old sessions
            _scalar_result(active_row),                       # Select active for tok-1
            _scalar_result(newest_row),                       # Select remaining newest
        ]

        result = await store.cleanup_old_sessions(86400)
        assert result == 2
        db.commit.assert_awaited_once()

    async def test_cleanup_nothing_to_remove(self, mock_redis):
        db = _make_mock_db()
        factory = _make_session_factory(db)
        store = SessionStore(factory, mock_redis)

        db.execute.side_effect = [MagicMock(all=MagicMock(return_value=[]))]

        result = await store.cleanup_old_sessions(86400)
        assert result == 0
