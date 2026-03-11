"""iFlytek Gateway Storage backend using aiohttp + HMAC-SHA256 auth."""

from __future__ import annotations

import json
import time
from typing import BinaryIO, Union
from urllib.parse import urlencode

import aiohttp

from infra.config import StorageConfig
from infra.log import logger
from infra.storage.base import ObjectStorage
from infra.storage.hmac_auth import build_auth_header


class IFlyGatewayStorage(ObjectStorage):
    """Object storage via iFlytek Gateway with HMAC-SHA256 authentication.

    Uses a persistent ``aiohttp.ClientSession`` (with built-in TCP connection
    pooling) that is created in ``start()`` and closed in ``close()``.
    """

    def __init__(self, config: StorageConfig):
        self._config = config
        self._session: aiohttp.ClientSession | None = None

    # -- Lifecycle -------------------------------------------------------------

    async def start(self) -> None:
        self._session = aiohttp.ClientSession()
        logger.info("iFlytek Gateway client initialised")

    async def close(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None
            logger.info("iFlytek Gateway client closed")

    async def ensure_bucket(self) -> None:
        """iFlytek Gateway does not require pre-creating buckets."""

    # -- Object operations -----------------------------------------------------

    async def put_object(
        self,
        key: str,
        body: Union[bytes, BinaryIO],
        content_type: str,
        content_length: int | None = None,
    ) -> str:
        if self._session is None:
            raise RuntimeError("IFlyGatewayStorage not started — call start() first")

        # Extract filename from key (last path segment)
        filename = key.rsplit("/", 1)[-1] if "/" in key else key

        # Read BinaryIO into bytes if needed
        if isinstance(body, bytes):
            file_bytes = body
        else:
            file_bytes = body.read()
            if hasattr(body, "seek"):
                body.seek(0)

        # Build request URL
        params = {
            "get_link": "true",
            "link_ttl": self._config.ttl,
            "filename": filename,
            "expose": "true",
        }
        base_url = f"{self._config.endpoint}/api/v1/{self._config.bucket}"
        url = base_url + "?" + urlencode(params)

        # Sign request
        headers = build_auth_header(
            url,
            method="POST",
            api_key=self._config.access_key,
            api_secret=self._config.secret_key,
        )
        headers["X-TTL"] = str(self._config.ttl)
        headers["Content-Length"] = str(len(file_bytes))

        t0 = time.time()
        try:
            async with self._session.post(url, headers=headers, data=file_bytes) as resp:
                response_text = await resp.text()
                if resp.status != 200:
                    raise RuntimeError(
                        f"iFlytek Gateway upload failed: "
                        f"status={resp.status}, body={response_text[:500]}"
                    )

                try:
                    ret = json.loads(response_text)
                except json.JSONDecodeError as e:
                    raise RuntimeError(
                        f"iFlytek Gateway returned invalid JSON: {response_text[:200]}"
                    ) from e

                if ret.get("code") != 0:
                    raise RuntimeError(
                        f"iFlytek Gateway upload rejected: "
                        f"code={ret.get('code')}, body={response_text}"
                    )

                try:
                    link = ret["data"]["link"]
                except KeyError as e:
                    raise RuntimeError(
                        f"iFlytek Gateway response missing expected field: {e}, body={response_text}"
                    ) from e

            elapsed = time.time() - t0
            logger.info(
                "iFlytek put: key={} size={} took={:.1f}ms",
                key, len(file_bytes), elapsed * 1000,
            )
            return link
        except Exception:
            elapsed = time.time() - t0
            logger.exception("iFlytek put failed: key={} took={:.1f}ms", key, elapsed * 1000)
            raise

    @property
    def bucket(self) -> str:
        return self._config.bucket
