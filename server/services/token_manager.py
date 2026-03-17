import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, delete, update, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from infra.models import Token
from infra.log import logger

# MySQL DATETIME maximum — used for "never expires" tokens.
_NEVER_EXPIRES = datetime(9999, 12, 31, 23, 59, 59, tzinfo=timezone.utc)


def _to_timestamp(dt: datetime) -> float:
    """Convert a (possibly naive) datetime to UTC Unix timestamp."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


class TokenManager:
    """MySQL-backed token management with sk- prefix and per-token expiry."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session = session_factory

    async def generate(self, name: str = "", expires_in: int = 0) -> str:
        token_value = "sk-" + secrets.token_hex(24)
        now = datetime.now(timezone.utc)
        expires_at = _NEVER_EXPIRES if expires_in == 0 else now + timedelta(seconds=expires_in)

        async with self._session() as session:
            session.add(Token(
                token=token_value,
                created_at=now,
                name=name,
                expires_at=expires_at,
            ))
            await session.commit()

        logger.info("Token generated: {}... (name={}, expires_in={}s)", token_value[:16], name, expires_in)
        return token_value

    async def validate(self, token: str | None) -> bool:
        if not token:
            return False
        async with self._session() as session:
            row = await session.execute(
                select(Token.token).where(
                    Token.token == token,
                    Token.expires_at >= datetime.now(timezone.utc),
                )
            )
            valid = row.scalar_one_or_none() is not None
        if valid:
            logger.debug("Token validated: {}...", token[:10])
        else:
            logger.debug("Token validation failed: {}...", token[:10])
        return valid

    async def update(
        self, token: str, name: str | None = None, expires_in: int | None = None
    ) -> bool:
        async with self._session() as session:
            row = await session.execute(
                select(Token).where(Token.token == token)
            )
            obj = row.scalar_one_or_none()
            if obj is None:
                logger.warning("Token update failed: {}... not found", token[:16])
                return False
            if name is not None:
                obj.name = name
            if expires_in is not None:
                obj.expires_at = (
                    _NEVER_EXPIRES if expires_in == 0 else datetime.now(timezone.utc) + timedelta(seconds=expires_in)
                )
            await session.commit()
        return True

    async def remove(self, token: str) -> None:
        async with self._session() as session:
            await session.execute(
                delete(Token).where(Token.token == token)
            )
            await session.commit()
        from infra.token_auth import invalidate_token_cache
        await invalidate_token_cache(token)
        logger.info("Token removed: {}...", token[:16])

    async def list_all(
        self,
        page: int = 1,
        page_size: int = 20,
        search: str = "",
    ) -> dict:
        now = datetime.now(timezone.utc)
        async with self._session() as session:
            base = select(Token).where(Token.expires_at >= now)
            if search:
                base = base.where(Token.token.contains(search))

            count_result = await session.execute(
                select(func.count()).select_from(base.subquery())
            )
            total = count_result.scalar() or 0

            offset = (page - 1) * page_size
            result = await session.execute(
                base.order_by(Token.created_at.desc())
                .limit(page_size)
                .offset(offset)
            )
            rows = result.scalars().all()

        return {
            "items": [
                {
                    "token": row.token,
                    "created_at": _to_timestamp(row.created_at),
                    "name": row.name or "",
                    "expires_at": _to_timestamp(row.expires_at),
                }
                for row in rows
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    async def cleanup_expired(self) -> int:
        async with self._session() as session:
            result = await session.execute(
                delete(Token).where(Token.expires_at < datetime.now(timezone.utc))
            )
            await session.commit()
            count = result.rowcount
        if count > 0:
            logger.info("Cleaned up {} expired tokens", count)
        return count
