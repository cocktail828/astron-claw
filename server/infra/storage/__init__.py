"""Object storage abstraction layer.

Usage::

    from infra.storage import ObjectStorage, create_storage

    storage: ObjectStorage = create_storage(config.storage)
    await storage.start()
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from infra.log import logger
from infra.storage.base import ObjectStorage

if TYPE_CHECKING:
    from infra.config import StorageConfig

__all__ = ["ObjectStorage", "create_storage"]


def create_storage(config: StorageConfig) -> ObjectStorage:
    """Create a storage backend based on *config.type*.

    Lazy-imports concrete implementations to avoid pulling in
    unnecessary dependencies (e.g. aiobotocore when using ifly_gateway).
    """
    if config.type == "s3":
        from infra.storage.s3 import S3Storage

        logger.info("Storage backend: {} (endpoint={})", config.type, config.endpoint)
        return S3Storage(config)
    if config.type == "ifly_gateway":
        from infra.storage.ifly_gateway import IFlyGatewayStorage

        logger.info("Storage backend: {} (endpoint={})", config.type, config.endpoint)
        return IFlyGatewayStorage(config)
    raise ValueError(f"Unknown storage type: '{config.type}'")
