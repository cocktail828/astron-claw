"""Abstract base class for object storage backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import BinaryIO, Union


class ObjectStorage(ABC):
    """Unified interface for object storage providers (S3, iFlytek Gateway, etc.)."""

    @abstractmethod
    async def start(self) -> None:
        """Initialize connections. Call once at application startup."""

    @abstractmethod
    async def close(self) -> None:
        """Release resources. Call once at application shutdown."""

    @abstractmethod
    async def ensure_bucket(self) -> None:
        """Ensure the storage bucket/namespace exists and is ready."""

    @abstractmethod
    async def put_object(
        self,
        key: str,
        body: Union[bytes, BinaryIO],
        content_type: str,
        content_length: int | None = None,
    ) -> str:
        """Upload an object and return its public download URL.

        Args:
            key: Object key (e.g. "{session_id}/{filename}").
            body: File content — raw bytes or seekable file-like object.
            content_type: MIME type of the object.
            content_length: Optional byte length hint.

        Returns:
            Public download URL as a string.

        Raises:
            RuntimeError: If storage backend is not started or upload fails.
        """

    @property
    @abstractmethod
    def bucket(self) -> str:
        """Bucket or namespace name (used for logging)."""
