"""S3/MinIO storage backend using aiobotocore."""

import json
import time
from typing import TYPE_CHECKING, BinaryIO, Union
from urllib.parse import quote

from aiobotocore.session import get_session, AioSession
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from infra.config import StorageConfig
from infra.log import logger
from infra.storage.base import ObjectStorage

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


class S3Storage(ObjectStorage):
    """Thin async wrapper around aiobotocore for S3/MinIO operations.

    Uses a persistent client connection (created in ``start()``, closed in
    ``close()``) to avoid per-request TCP handshake overhead.
    """

    def __init__(self, config: StorageConfig):
        self._config = config
        self._session: AioSession = get_session()
        self._client_ctx = None
        self._client: "S3Client | None" = None

    # -- Lifecycle -------------------------------------------------------------

    async def start(self) -> None:
        self._client_ctx = self._session.create_client(
            "s3",
            endpoint_url=self._config.endpoint,
            aws_access_key_id=self._config.access_key,
            aws_secret_access_key=self._config.secret_key,
            region_name=self._config.region,
            config=BotoConfig(
                signature_version="s3v4",
                s3={"payload_signing_enabled": False},
            ),
        )
        self._client = await self._client_ctx.__aenter__()
        logger.info("S3 client initialised (endpoint={})", self._config.endpoint)

    async def close(self) -> None:
        if self._client_ctx:
            await self._client_ctx.__aexit__(None, None, None)
            self._client_ctx = None
            self._client = None
            logger.info("S3 client closed")

    def _get_client(self) -> "S3Client":
        if self._client is None:
            raise RuntimeError("S3Storage not started — call start() first")
        return self._client

    # -- Bucket setup ----------------------------------------------------------

    async def ensure_bucket(self) -> None:
        """Create bucket if not exists; only configure policy and lifecycle for newly created buckets."""
        client = self._get_client()

        try:
            await client.head_bucket(Bucket=self._config.bucket)
            logger.info("S3 bucket '{}' already exists, skipping policy/lifecycle setup", self._config.bucket)
            return
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code")
            if error_code == "404":
                await client.create_bucket(Bucket=self._config.bucket)
                logger.info("S3 bucket '{}' created", self._config.bucket)
            else:
                raise

        # Only configure policy and lifecycle for newly created buckets
        # to avoid overwriting production settings on every restart
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

    # -- Object operations -----------------------------------------------------

    async def put_object(
        self,
        key: str,
        body: Union[bytes, BinaryIO],
        content_type: str,
        content_length: int | None = None,
    ) -> str:
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

        t0 = time.time()
        try:
            await client.put_object(**kwargs)
            elapsed = time.time() - t0
            logger.info("S3 put: key={} type={} took={:.1f}ms", key, content_type, elapsed * 1000)
        except Exception:
            elapsed = time.time() - t0
            logger.exception("S3 put failed: key={} took={:.1f}ms", key, elapsed * 1000)
            raise

        # URL-encode the key so that filenames with spaces, CJK chars, #, ?
        # etc. produce a valid download URL.
        encoded_key = quote(key, safe="/")
        return f"{self._config.public_endpoint}/{self._config.bucket}/{encoded_key}"

    @property
    def bucket(self) -> str:
        return self._config.bucket
