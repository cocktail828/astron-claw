"""SessionStore — MySQL as source of truth, Redis as write-through cache.

Write path: MySQL first (must succeed) → Redis cache (failure only warns).
Read path: Redis first → miss → MySQL query + cache repopulate.
"""

import time
from typing import Optional

from redis.asyncio import Redis
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from infra.log import logger
from infra.models import ChatSession

_SESSIONS_PREFIX = "bridge:sessions:"
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
            await db.commit()

        # Redis — best effort
        try:
            sessions_key = f"{_SESSIONS_PREFIX}{token}"
            await self._redis.rpush(sessions_key, session_id)
            await self._redis.expire(sessions_key, _CACHE_TTL)
        except Exception:
            logger.warning("Redis cache write failed for create_session (token={}...)", token[:10])

        return session_number

    async def remove_sessions(self, token: str) -> None:
        """Delete all session data for a token from both MySQL and Redis."""
        async with self._sf() as db:
            await db.execute(delete(ChatSession).where(ChatSession.token == token))
            await db.commit()

        try:
            await self._redis.delete(f"{_SESSIONS_PREFIX}{token}")
        except Exception:
            logger.warning("Redis cache delete failed for remove_sessions (token={}...)", token[:10])

    # ── Read operations (cache-aside) ─────────────────────────────────────────

    async def get_session(self, token: str, session_id: str) -> Optional[tuple[str, int]]:
        """Return (session_id, session_number) if it belongs to token, else None.

        Uses the unique index on session_id for O(1) lookup.
        """
        async with self._sf() as db:
            result = await db.execute(
                select(ChatSession.session_id, ChatSession.session_number)
                .where(ChatSession.token == token, ChatSession.session_id == session_id)
            )
            row = result.one_or_none()
        if row is None:
            return None
        return (row.session_id, row.session_number)

    async def get_sessions(self, token: str) -> list[tuple[str, int]]:
        """Return [(session_id, number), ...] for the token."""
        # Try Redis first
        try:
            sessions_key = f"{_SESSIONS_PREFIX}{token}"
            cached_sessions = await self._redis.lrange(sessions_key, 0, -1)
            if cached_sessions:
                return [(sid, i + 1) for i, sid in enumerate(cached_sessions)]
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
            return []

        numbered = [(r.session_id, r.session_number) for r in rows]

        # Repopulate cache
        await self._repopulate_cache(token, rows)
        return numbered

    # ── Cleanup ───────────────────────────────────────────────────────────────

    async def cleanup_old_sessions(self, max_age_seconds: float) -> int:
        """Delete sessions older than max_age_seconds. Returns count removed."""
        cutoff = time.time() - max_age_seconds
        async with self._sf() as db:
            result = await db.execute(
                select(ChatSession.token, ChatSession.session_id)
                .where(ChatSession.created_at < cutoff)
            )
            old_sessions = result.all()
            if not old_sessions:
                return 0

            await db.execute(
                delete(ChatSession).where(ChatSession.created_at < cutoff)
            )

            # Invalidate Redis cache for affected tokens
            affected_tokens = {row.token for row in old_sessions}
            for token in affected_tokens:
                try:
                    await self._redis.delete(f"{_SESSIONS_PREFIX}{token}")
                except Exception:
                    logger.warning("Redis cache invalidation failed during session cleanup (token={}...)", token[:10])

            await db.commit()
            count = len(old_sessions)

        logger.info("Cleaned up {} old sessions (cutoff={})", count, cutoff)
        return count

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _repopulate_cache(self, token: str, rows=None) -> None:
        """Rebuild Redis cache from MySQL using a pipeline."""
        if rows is None:
            async with self._sf() as db:
                result = await db.execute(
                    select(ChatSession)
                    .where(ChatSession.token == token)
                    .order_by(ChatSession.session_number)
                )
                rows = result.scalars().all()

        sessions_key = f"{_SESSIONS_PREFIX}{token}"

        try:
            pipe = self._redis.pipeline()
            pipe.delete(sessions_key)
            if rows:
                pipe.rpush(sessions_key, *[r.session_id for r in rows])
                pipe.expire(sessions_key, _CACHE_TTL)
            await pipe.execute()
        except Exception:
            logger.warning("Redis cache repopulate failed (token={}...)", token[:10])
