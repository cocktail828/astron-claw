"""Async S3 client wrapper using aiobotocore."""

import json
from typing import TYPE_CHECKING, BinaryIO, Union
from urllib.parse import quote

from aiobotocore.session import get_session, AioSession
from botocore.exceptions import ClientError

from infra.config import S3Config
from infra.log import logger

if TYPE_CHECKING:
    from types_aiobotocore_s3 import S3Client

_LIFECYCLE_RULE_7D = {
    "Rules": [{
        "ID": "expire-media-7d",
        "Status": "Enabled",
        "Expiration": {"Days": 7},
        "Filter": {"Prefix": ""},
    }],
}


class S3Storage:
    """Thin async wrapper around aiobotocore for S3/MinIO operations.

    Uses a persistent client connection (created in ``start()``, closed in
    ``close()``) to avoid per-request TCP handshake overhead.
    """

    def __init__(self, config: S3Config):
        self._config = config
        self._session: AioSession = get_session()
        self._client_ctx = None
        self._client: "S3Client | None" = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Create the persistent S3 client (call once at startup)."""
        self._client_ctx = self._session.create_client(
            "s3",
            endpoint_url=self._config.endpoint,
            aws_access_key_id=self._config.access_key,
            aws_secret_access_key=self._config.secret_key,
            region_name=self._config.region,
        )
        self._client = await self._client_ctx.__aenter__()

    async def close(self) -> None:
        """Close the persistent S3 client (call once at shutdown)."""
        if self._client_ctx:
            await self._client_ctx.__aexit__(None, None, None)
            self._client_ctx = None
            self._client = None

    def _get_client(self) -> "S3Client":
        if self._client is None:
            raise RuntimeError("S3Storage not started — call start() first")
        return self._client

    # ── Bucket setup ──────────────────────────────────────────────────────────

    async def ensure_bucket(self) -> None:
        """Create bucket if not exists, then ensure public-read policy and lifecycle."""
        client = self._get_client()

        try:
            await client.head_bucket(Bucket=self._config.bucket)
            logger.info("S3 bucket '{}' already exists", self._config.bucket)
        except ClientError as e:
            error_code = int(e.response.get("Error", {}).get("Code", 0))
            if error_code == 404:
                await client.create_bucket(Bucket=self._config.bucket)
                logger.info("S3 bucket '{}' created", self._config.bucket)
            else:
                raise

        # Always ensure public-read policy and lifecycle (idempotent)
        policy = {
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": "*",
                "Action": "s3:GetObject",
                "Resource": f"arn:aws:s3:::{self._config.bucket}/*",
            }],
        }
        await client.put_bucket_policy(
            Bucket=self._config.bucket,
            Policy=json.dumps(policy),
        )
        await client.put_bucket_lifecycle_configuration(
            Bucket=self._config.bucket,
            LifecycleConfiguration=_LIFECYCLE_RULE_7D,
        )
        logger.info("S3 bucket '{}' configured (public-read + 7d lifecycle)", self._config.bucket)

    # ── Object operations ─────────────────────────────────────────────────────

    async def put_object(
        self,
        key: str,
        body: Union[bytes, BinaryIO],
        content_type: str,
        content_length: int | None = None,
    ) -> str:
        """Upload data to S3 and return the public download URL.

        ``body`` can be raw bytes *or* a seekable file-like object (e.g.
        ``SpooledTemporaryFile`` from FastAPI ``UploadFile``).  Passing a
        file object avoids holding the entire payload in memory.
        """
        client = self._get_client()

        # Ensure text types include charset so browsers decode CJK etc. correctly
        if content_type.startswith("text/") and "charset" not in content_type:
            content_type = f"{content_type}; charset=utf-8"

        kwargs: dict = {
            "Bucket": self._config.bucket,
            "Key": key,
            "Body": body,
            "ContentType": content_type,
        }
        if content_length is not None:
            kwargs["ContentLength"] = content_length

        await client.put_object(**kwargs)

        # URL-encode the key so that filenames with spaces, CJK chars, #, ?
        # etc. produce a valid download URL.
        encoded_key = quote(key, safe="/")
        return f"{self._config.public_endpoint}/{self._config.bucket}/{encoded_key}"

    @property
    def bucket(self) -> str:
        return self._config.bucket
