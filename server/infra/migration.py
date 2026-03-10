"""Auto-run Alembic migrations on application startup.

Handles:
- Distributed locking via Redis (standalone + cluster) so only one worker migrates.
- Graceful skip when the DB account lacks DDL privileges.
- Other workers wait for the migration to finish before continuing startup.
- On migration failure, waiting workers are notified immediately (fail-fast).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path
from typing import Union

from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from redis.asyncio import Redis, RedisCluster
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

from infra.log import logger

_LOCK_KEY = "migrate:lock"
_DONE_KEY = "migrate:done"
_FAIL_KEY = "migrate:failed"
_LOCK_TTL = 60  # seconds
_DONE_TTL = 300  # seconds
_WAIT_INTERVAL = 1  # seconds
_WAIT_TIMEOUT = 60  # seconds

# MySQL error codes indicating missing DDL privileges
_DDL_DENIED_CODES = (1142, 1044, 1227, 1370)

# Lua script: atomically release lock only if we own it
_RELEASE_LOCK_LUA = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
    return redis.call("DEL", KEYS[1])
else
    return 0
end
"""

# ---------------------------------------------------------------------------
# Alembic helpers (run inside asyncio.to_thread — each gets its own event loop)
# ---------------------------------------------------------------------------

def _get_alembic_config() -> AlembicConfig:
    """Build an AlembicConfig pointing to the project's alembic.ini."""
    server_dir = Path(__file__).resolve().parent.parent
    ini_path = server_dir / "alembic.ini"
    cfg = AlembicConfig(str(ini_path))
    cfg.set_main_option("script_location", str(server_dir / "migrations"))
    return cfg


async def _async_get_current_revision(db_url: str) -> str | None:
    """Get the current migration revision from the database (async, aiomysql)."""
    engine = create_async_engine(db_url, poolclass=pool.NullPool)
    try:
        async with engine.connect() as conn:
            return await conn.run_sync(
                lambda c: MigrationContext.configure(c).get_current_revision()
            )
    finally:
        await engine.dispose()


def _get_pending_revisions(db_url: str) -> list[str]:
    """Return list of pending migration revision IDs.

    Called via asyncio.to_thread — uses asyncio.run() for async DB access
    with the same aiomysql driver as env.py, avoiding a pymysql dependency.
    """
    current_rev = asyncio.run(_async_get_current_revision(db_url))

    cfg = _get_alembic_config()
    script = ScriptDirectory.from_config(cfg)
    head = script.get_current_head()

    if current_rev == head:
        return []
    return [r.revision for r in script.iterate_revisions(head, current_rev)
            if r.revision != current_rev]


def _execute_upgrade() -> None:
    """Run ``alembic upgrade head`` synchronously.

    NOTE: The DB URL is NOT passed through Alembic's configparser to avoid
    percent-encoding interpolation issues. Instead, migrations/env.py reads
    the URL directly via load_config() — same source as the app.
    """
    cfg = _get_alembic_config()
    alembic_command.upgrade(cfg, "head")


def _is_ddl_denied(exc: Exception) -> bool:
    """Check if the exception is a MySQL DDL permission denial."""
    if hasattr(exc, "orig") and hasattr(exc.orig, "args"):
        code = exc.orig.args[0] if exc.orig.args else None
        return code in _DDL_DENIED_CODES
    msg = str(exc).lower()
    return any(kw in msg for kw in ("access denied", "command denied", "1142", "1044"))


# ---------------------------------------------------------------------------
# Redis distributed lock helpers
# ---------------------------------------------------------------------------

async def _acquire_lock(redis: Union[Redis, RedisCluster], owner: str) -> bool:
    """Try to acquire the migration lock. Returns True on success."""
    result = await redis.set(_LOCK_KEY, owner, nx=True, ex=_LOCK_TTL)
    return result is True


async def _release_lock(redis: Union[Redis, RedisCluster], owner: str) -> None:
    """Release the lock only if we still own it (atomic via Lua)."""
    await redis.eval(_RELEASE_LOCK_LUA, 1, _LOCK_KEY, owner)


async def _mark_done(redis: Union[Redis, RedisCluster]) -> None:
    """Set the migration-done marker so waiting workers can proceed."""
    await redis.set(_DONE_KEY, "1", ex=_DONE_TTL)


async def _mark_failed(redis: Union[Redis, RedisCluster], msg: str) -> None:
    """Set the migration-failed marker so waiting workers can fail fast."""
    await redis.set(_FAIL_KEY, msg[:500], ex=_DONE_TTL)


async def _wait_for_done(redis: Union[Redis, RedisCluster]) -> bool:
    """Poll for the migration-done/failed marker.

    Returns True if migration completed successfully.
    Raises RuntimeError if another worker's migration failed.
    """
    deadline = time.monotonic() + _WAIT_TIMEOUT
    while time.monotonic() < deadline:
        if await redis.exists(_DONE_KEY):
            return True
        fail_msg = await redis.get(_FAIL_KEY)
        if fail_msg is not None:
            raise RuntimeError(f"Migration failed on another worker: {fail_msg}")
        await asyncio.sleep(_WAIT_INTERVAL)
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def run_migrations(redis: Union[Redis, RedisCluster], db_url: str) -> None:
    """Run pending Alembic migrations with distributed locking.

    Called during application lifespan startup, after DB and Redis are ready.

    Behaviour:
    - If no pending migrations exist, return immediately (no lock needed).
    - Acquire a Redis lock; the winner runs ``alembic upgrade head``.
    - On DDL permission error, log a warning and continue startup.
    - On other errors, mark failure so waiting workers fail fast.
    - Losers wait for a ``migrate:done`` marker before continuing.
    """
    # Fast path: check for pending migrations without acquiring a lock
    try:
        pending = await asyncio.to_thread(_get_pending_revisions, db_url)
    except Exception as exc:
        logger.warning("Unable to check migration status, skipping auto-migrate: {}", exc)
        return

    if not pending:
        logger.info("Database schema is up to date, no migrations to run")
        return

    logger.info("Found {} pending migration(s): {}", len(pending), pending)

    owner = uuid.uuid4().hex
    acquired = await _acquire_lock(redis, owner)

    if acquired:
        logger.info("Acquired migration lock, running alembic upgrade head ...")
        try:
            # Run in a thread — env.py uses asyncio.run() which needs its own loop
            await asyncio.to_thread(_execute_upgrade)
            logger.info("Database migration completed successfully")
            await _mark_done(redis)
        except Exception as exc:
            if _is_ddl_denied(exc):
                logger.warning(
                    "Database account lacks DDL privileges, skipping migration. "
                    "Please run 'alembic upgrade head' manually with a privileged account."
                )
                await _mark_done(redis)
            else:
                logger.error("Database migration failed: {}", exc)
                await _mark_failed(redis, str(exc))
                raise
        finally:
            await _release_lock(redis, owner)
    else:
        logger.info("Another worker is running migrations, waiting ...")
        if await _wait_for_done(redis):
            logger.info("Migration completed by another worker, proceeding with startup")
        else:
            logger.warning(
                "Timed out waiting for migration to complete ({}s). "
                "Proceeding with startup — schema may be outdated.",
                _WAIT_TIMEOUT,
            )
