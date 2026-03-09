import uuid
from pathlib import Path
from typing import BinaryIO

from infra.log import logger
from infra.s3 import S3Storage

MAX_FILE_SIZE = 500 * 1024 * 1024  # 500 MB

ALLOWED_MIME_PREFIXES = (
    "image/",
    "audio/",
    "video/",
    "application/pdf",
    "application/zip",
    "application/octet-stream",
    "text/",
)


class MediaManager:
    """Manages file upload to S3 object storage."""

    def __init__(self, s3: S3Storage):
        self._s3 = s3

    async def store(
        self,
        file_obj: BinaryIO,
        file_name: str,
        file_size: int,
        mime_type: str,
        session_id: str | None = None,
    ) -> dict | None:
        """Upload a file to S3.

        ``file_obj`` is a seekable file-like object (e.g. ``SpooledTemporaryFile``
        from FastAPI ``UploadFile.file``).  The caller is responsible for
        seeking to the start before calling this method.
        """
        if file_size > MAX_FILE_SIZE:
            logger.warning("Media rejected: file too large ({} bytes, max={})", file_size, MAX_FILE_SIZE)
            return None
        if file_size == 0:
            logger.warning("Media rejected: empty file (name={})", file_name)
            return None
        if not self._is_mime_allowed(mime_type):
            logger.warning("Media rejected: unsupported MIME type '{}' (name={})", mime_type, file_name)
            return None

        # Sanitize filename (prevent path traversal and bare dot-segments)
        safe_name = Path(file_name).name
        if not safe_name or safe_name.startswith("."):
            safe_name = "unnamed"

        # Use provided sessionId or generate a random UUID
        sid = session_id or uuid.uuid4().hex
        # S3 key uses the raw filename; URL encoding is handled by S3Storage
        # when constructing the public download URL.
        key = f"{sid}/{safe_name}"

        download_url = await self._s3.put_object(key, file_obj, mime_type, file_size)

        logger.info(
            "Stored media s3://{}/{} ({}, {} bytes)",
            self._s3.bucket, key, mime_type, file_size,
        )
        return {
            "fileName": safe_name,
            "mimeType": mime_type,
            "fileSize": file_size,
            "sessionId": sid,
            "downloadUrl": download_url,
        }

    def _is_mime_allowed(self, mime_type: str) -> bool:
        if not mime_type:
            return False
        for prefix in ALLOWED_MIME_PREFIXES:
            if mime_type.startswith(prefix):
                return True
        return False
