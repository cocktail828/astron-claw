"""Token authentication ASGI middleware.

Centralises Bearer token extraction from ``X-Api-Key`` / ``Authorization``
headers and token validation so that individual route handlers no longer
need per-endpoint auth boilerplate.

Token validation results are cached in Redis to avoid hitting MySQL on
every request.  A valid token is cached for ``_CACHE_TTL`` seconds;
a missing/invalid token always falls through to MySQL so that newly
created tokens are recognised immediately.

Protected paths receive a validated ``request.state.token``; all others
pass through untouched.
"""

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from infra.cache import get_redis
import services.state as state

# Path prefixes that require token authentication.
_PROTECTED_PREFIXES = ("/bridge/", "/api/media/")

# Redis key prefix and TTL for token validation cache.
_CACHE_PREFIX = "token_auth:"
_CACHE_TTL = 30  # seconds


def _cache_key(token: str) -> str:
    return f"{_CACHE_PREFIX}{token}"


async def invalidate_token_cache(token: str) -> None:
    """Remove a token from the auth cache so it is re-validated on next request."""
    redis = get_redis()
    await redis.delete(_cache_key(token))


def _extract_bearer(raw: str | None) -> str | None:
    """Return the token from a ``Bearer <token>`` header value, or *None*."""
    if not raw or not raw.lower().startswith("bearer "):
        return None
    token = raw[7:].strip()
    return token or None


class TokenAuthMiddleware:
    """Validate Bearer token for protected paths.

    * Reads ``X-Api-Key`` or ``Authorization`` (equal priority, first found wins).
    * Checks Redis cache first; on miss, falls through to MySQL via
      :pyattr:`state.token_manager` and caches the positive result.
    * On success, stores the token in ``scope["state"]["token"]`` so handlers
      can access it via ``request.state.token``.
    * On failure, short-circuits with a **401** JSON response.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self._is_protected(scope.get("path", "")):
            await self.app(scope, receive, send)
            return

        # Build a quick lookup {lower_name: value}
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        raw = headers.get(b"x-api-key") or headers.get(b"authorization")
        raw_str = raw.decode("latin-1") if raw else None

        token = _extract_bearer(raw_str)

        if not token or not await self._validate_cached(token):
            resp = JSONResponse(
                status_code=401,
                content={"code": 401, "error": "Invalid or missing token"},
            )
            await resp(scope, receive, send)
            return

        scope.setdefault("state", {})["token"] = token
        await self.app(scope, receive, send)

    # ── helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _is_protected(path: str) -> bool:
        return any(path.startswith(p) for p in _PROTECTED_PREFIXES)

    @staticmethod
    async def _validate_cached(token: str) -> bool:
        """Return *True* if the token is valid, using Redis as a cache."""
        redis = get_redis()
        key = _cache_key(token)

        # Fast path: cache hit
        cached = await redis.get(key)
        if cached == "1":
            return True

        # Slow path: ask MySQL
        valid = await state.token_manager.validate(token)
        if valid:
            await redis.set(key, "1", ex=_CACHE_TTL)
        return valid
