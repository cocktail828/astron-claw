"""SessionStore — MySQL as source of truth, Redis as write-through cache.

Write path: MySQL first (must succeed) → Redis cache (failure only warns).
Read path: Redis first → miss → MySQL query + cache repopulate.
Lazy migration: if Redis has data but MySQL doesn't, auto-migrate on first read.
"""

import time
from typing import Optional

from redis.asyncio import Redis
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from infra.log import logger
from infra.models import ChatActiveSession, ChatSession

_SESSIONS_PREFIX = "bridge:sessions:"
_ACTIVE_PREFIX = "bridge:active:"
_CACHE_TTL = 3600  # 1 hour


class SessionStore:
    """Encapsulates session CRUD with MySQL persistence and Redis caching."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], redis: Redis):
        self._sf = session_factory
        self._redis = redis

    # ── Write operations ──────────────────────────────────────────────────────

    async def create_session(self, token: str, session_id: str) -> int:
        """Persist a new session to MySQL and cache in Redis.

        Returns the session_number (1-based ordinal within the token).
        """
        now = time.time()

        # MySQL — must succeed
        async with self._sf() as db:
            # Determine next session number
            result = await db.execute(
                select(func.coalesce(func.max(ChatSession.session_number), 0))
                .where(ChatSession.token == token)
            )
            max_num = result.scalar()
            session_number = max_num + 1

            db.add(ChatSession(
                token=token,
                session_id=session_id,
                session_number=session_number,
                created_at=now,
            ))

            # Upsert active session
            existing = (await db.execute(
                select(ChatActiveSession).where(ChatActiveSession.token == token)
            )).scalar_one_or_none()
            if existing:
                existing.active_session_id = session_id
                existing.updated_at = now
            else:
                db.add(ChatActiveSession(
                    token=token,
                    active_session_id=session_id,
                    updated_at=now,
                ))
            await db.commit()

        # Redis — best effort
        try:
            sessions_key = f"{_SESSIONS_PREFIX}{token}"
            active_key = f"{_ACTIVE_PREFIX}{token}"
            await self._redis.rpush(sessions_key, session_id)
            await self._redis.set(active_key, session_id)
            await self._redis.expire(sessions_key, _CACHE_TTL)
            await self._redis.expire(active_key, _CACHE_TTL)
        except Exception:
            logger.warning("Redis cache write failed for create_session (token={}...)", token[:10])

        return session_number

    async def switch_session(self, token: str, session_id: str) -> bool:
        """Switch active session. Returns False if session_id doesn't belong to token."""
        now = time.time()

        # Verify existence in MySQL
        async with self._sf() as db:
            result = await db.execute(
                select(ChatSession)
                .where(ChatSession.token == token, ChatSession.session_id == session_id)
            )
            if result.scalar_one_or_none() is None:
                logger.warning("Session switch failed: {} not in MySQL (token={}...)", session_id[:8], token[:10])
                return False

            # Update active session
            existing = (await db.execute(
                select(ChatActiveSession).where(ChatActiveSession.token == token)
            )).scalar_one_or_none()
            if existing:
                existing.active_session_id = session_id
                existing.updated_at = now
            else:
                db.add(ChatActiveSession(
                    token=token,
                    active_session_id=session_id,
                    updated_at=now,
                ))
            await db.commit()

        # Update Redis cache
        try:
            active_key = f"{_ACTIVE_PREFIX}{token}"
            await self._redis.set(active_key, session_id)
            await self._redis.expire(active_key, _CACHE_TTL)
        except Exception:
            logger.warning("Redis cache write failed for switch_session (token={}...)", token[:10])

        return True

    async def remove_sessions(self, token: str) -> None:
        """Delete all session data for a token from both MySQL and Redis."""
        async with self._sf() as db:
            await db.execute(delete(ChatSession).where(ChatSession.token == token))
            await db.execute(delete(ChatActiveSession).where(ChatActiveSession.token == token))
            await db.commit()

        try:
            await self._redis.delete(f"{_SESSIONS_PREFIX}{token}")
            await self._redis.delete(f"{_ACTIVE_PREFIX}{token}")
        except Exception:
            logger.warning("Redis cache delete failed for remove_sessions (token={}...)", token[:10])

    # ── Read operations (cache-aside) ─────────────────────────────────────────

    async def get_active_session(self, token: str) -> Optional[str]:
        """Return the active session ID, or None if no session exists."""
        # Try Redis first
        try:
            active_key = f"{_ACTIVE_PREFIX}{token}"
            cached = await self._redis.get(active_key)
            if cached is not None:
                # Detect legacy key without TTL and trigger migration
                ttl = await self._redis.ttl(active_key)
                if ttl == -1:  # no expiry → pre-migration data
                    await self._maybe_migrate_from_redis(token)
                return cached
        except Exception:
            logger.warning("Redis read failed for get_active_session (token={}...)", token[:10])

        # Cache miss — query MySQL
        async with self._sf() as db:
            result = await db.execute(
                select(ChatActiveSession).where(ChatActiveSession.token == token)
            )
            row = result.scalar_one_or_none()

        if row is None:
            # Check for lazy migration
            await self._maybe_migrate_from_redis(token)
            # Re-query after potential migration
            async with self._sf() as db:
                result = await db.execute(
                    select(ChatActiveSession).where(ChatActiveSession.token == token)
                )
                row = result.scalar_one_or_none()
            if row is None:
                return None

        # Repopulate cache
        await self._repopulate_cache(token)
        return row.active_session_id

    async def get_sessions(self, token: str) -> tuple[list[tuple[str, int]], str]:
        """Return ([(session_id, number), ...], active_id) for the token."""
        # Try Redis first
        try:
            sessions_key = f"{_SESSIONS_PREFIX}{token}"
            cached_sessions = await self._redis.lrange(sessions_key, 0, -1)
            if cached_sessions:
                # Detect legacy key without TTL and trigger migration
                ttl = await self._redis.ttl(sessions_key)
                if ttl == -1:  # no expiry → pre-migration data
                    await self._maybe_migrate_from_redis(token)
                numbered = [(sid, i + 1) for i, sid in enumerate(cached_sessions)]
                active_id = await self._redis.get(f"{_ACTIVE_PREFIX}{token}") or ""
                return numbered, active_id
        except Exception:
            logger.warning("Redis read failed for get_sessions (token={}...)", token[:10])

        # Cache miss — query MySQL
        async with self._sf() as db:
            result = await db.execute(
                select(ChatSession)
                .where(ChatSession.token == token)
                .order_by(ChatSession.session_number)
            )
            rows = result.scalars().all()

        if not rows:
            # Check for lazy migration
            await self._maybe_migrate_from_redis(token)
            async with self._sf() as db:
                result = await db.execute(
                    select(ChatSession)
                    .where(ChatSession.token == token)
                    .order_by(ChatSession.session_number)
                )
                rows = result.scalars().all()
            if not rows:
                return [], ""

        numbered = [(r.session_id, r.session_number) for r in rows]

        # Get active session
        async with self._sf() as db:
            active_result = await db.execute(
                select(ChatActiveSession).where(ChatActiveSession.token == token)
            )
            active_row = active_result.scalar_one_or_none()
        active_id = active_row.active_session_id if active_row else ""

        # Repopulate cache
        await self._repopulate_cache(token)
        return numbered, active_id

    # ── Cleanup ───────────────────────────────────────────────────────────────

    async def cleanup_old_sessions(self, max_age_seconds: float) -> int:
        """Delete sessions older than max_age_seconds. Returns count removed."""
        cutoff = time.time() - max_age_seconds
        async with self._sf() as db:
            # Find tokens that have sessions to clean up
            result = await db.execute(
                select(ChatSession.token, ChatSession.session_id)
                .where(ChatSession.created_at < cutoff)
            )
            old_sessions = result.all()
            if not old_sessions:
                return 0

            # Delete old sessions from MySQL
            await db.execute(
                delete(ChatSession).where(ChatSession.created_at < cutoff)
            )

            # For each affected token, check if the active session was removed
            affected_tokens = {row.token for row in old_sessions}
            removed_session_ids = {row.session_id for row in old_sessions}

            for token in affected_tokens:
                # Check if active session was among the removed ones
                active_result = await db.execute(
                    select(ChatActiveSession).where(ChatActiveSession.token == token)
                )
                active_row = active_result.scalar_one_or_none()
                if active_row and active_row.active_session_id in removed_session_ids:
                    # Check if there are remaining sessions
                    remaining = await db.execute(
                        select(ChatSession)
                        .where(ChatSession.token == token)
                        .order_by(ChatSession.session_number.desc())
                        .limit(1)
                    )
                    newest = remaining.scalar_one_or_none()
                    if newest:
                        active_row.active_session_id = newest.session_id
                        active_row.updated_at = time.time()
                    else:
                        await db.delete(active_row)

                # Invalidate Redis cache for affected tokens
                try:
                    await self._redis.delete(f"{_SESSIONS_PREFIX}{token}")
                    await self._redis.delete(f"{_ACTIVE_PREFIX}{token}")
                except Exception:
                    pass

            await db.commit()
            count = len(old_sessions)

        logger.info("Cleaned up {} old sessions (cutoff={})", count, cutoff)
        return count

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _repopulate_cache(self, token: str) -> None:
        """Rebuild Redis cache from MySQL using a pipeline."""
        async with self._sf() as db:
            result = await db.execute(
                select(ChatSession)
                .where(ChatSession.token == token)
                .order_by(ChatSession.session_number)
            )
            rows = result.scalars().all()

            active_result = await db.execute(
                select(ChatActiveSession).where(ChatActiveSession.token == token)
            )
            active_row = active_result.scalar_one_or_none()

        sessions_key = f"{_SESSIONS_PREFIX}{token}"
        active_key = f"{_ACTIVE_PREFIX}{token}"

        try:
            pipe = self._redis.pipeline()
            pipe.delete(sessions_key)
            if rows:
                pipe.rpush(sessions_key, *[r.session_id for r in rows])
                pipe.expire(sessions_key, _CACHE_TTL)
            if active_row:
                pipe.set(active_key, active_row.active_session_id)
                pipe.expire(active_key, _CACHE_TTL)
            else:
                pipe.delete(active_key)
            await pipe.execute()
        except Exception:
            logger.warning("Redis cache repopulate failed (token={}...)", token[:10])

    async def _maybe_migrate_from_redis(self, token: str) -> None:
        """Lazy migration: if Redis has session data but MySQL doesn't, migrate."""
        try:
            sessions_key = f"{_SESSIONS_PREFIX}{token}"
            cached_sessions = await self._redis.lrange(sessions_key, 0, -1)
            if not cached_sessions:
                return
        except Exception:
            return  # Redis unavailable, nothing to migrate

        # Check if MySQL already has data (double-check to avoid re-migration)
        async with self._sf() as db:
            result = await db.execute(
                select(func.count()).select_from(ChatSession).where(ChatSession.token == token)
            )
            if result.scalar() > 0:
                return  # Already migrated

        # Migrate from Redis to MySQL
        now = time.time()
        try:
            active_id = await self._redis.get(f"{_ACTIVE_PREFIX}{token}")
        except Exception:
            active_id = None

        async with self._sf() as db:
            for i, session_id in enumerate(cached_sessions, 1):
                db.add(ChatSession(
                    token=token,
                    session_id=session_id,
                    session_number=i,
                    created_at=now,
                ))
            if active_id:
                db.add(ChatActiveSession(
                    token=token,
                    active_session_id=active_id,
                    updated_at=now,
                ))
            elif cached_sessions:
                # Default to last session as active
                db.add(ChatActiveSession(
                    token=token,
                    active_session_id=cached_sessions[-1],
                    updated_at=now,
                ))
            await db.commit()

        # Set TTL on legacy keys so migration doesn't re-trigger
        try:
            await self._redis.expire(f"{_SESSIONS_PREFIX}{token}", _CACHE_TTL)
            await self._redis.expire(f"{_ACTIVE_PREFIX}{token}", _CACHE_TTL)
        except Exception:
            pass

        logger.info(
            "Lazy-migrated {} sessions from Redis to MySQL (token={}...)",
            len(cached_sessions), token[:10],
        )
