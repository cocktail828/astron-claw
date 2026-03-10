import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the server directory
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path)


@dataclass(frozen=True)
class MysqlConfig:
    host: str
    port: int
    user: str
    password: str
    database: str

    @property
    def url(self) -> str:
        from urllib.parse import quote_plus
        pwd = quote_plus(self.password)
        return (
            f"mysql+aiomysql://{self.user}:{pwd}"
            f"@{self.host}:{self.port}/{self.database}?charset=utf8mb4"
        )


@dataclass(frozen=True)
class RedisConfig:
    host: str
    port: int
    password: str
    db: int
    cluster: bool


@dataclass(frozen=True)
class ServerConfig:
    host: str
    port: int
    workers: int
    log_level: str
    access_log: bool


@dataclass(frozen=True)
class QueueConfig:
    type: str
    max_stream_len: int
    block_ms: int


@dataclass(frozen=True)
class StorageConfig:
    type: str
    endpoint: str
    public_endpoint: str
    access_key: str
    secret_key: str
    bucket: str
    region: str = "us-east-1"
    ttl: int = 157788000


@dataclass(frozen=True)
class AppConfig:
    mysql: MysqlConfig
    redis: RedisConfig
    server: ServerConfig
    queue: QueueConfig
    storage: StorageConfig


from typing import Final

_VALID_OSS_TYPES: Final[tuple[str, ...]] = ("s3", "ifly_gateway")


def _validate_oss_type(value: str) -> str:
    if value not in _VALID_OSS_TYPES:
        raise ValueError(f"Invalid OSS_TYPE: '{value}' (must be one of {_VALID_OSS_TYPES})")
    return value


def load_config() -> AppConfig:
    return AppConfig(
        mysql=MysqlConfig(
            host=os.getenv("MYSQL_HOST", "127.0.0.1"),
            port=int(os.getenv("MYSQL_PORT", "3306")),
            user=os.getenv("MYSQL_USER", "root"),
            password=os.getenv("MYSQL_PASSWORD", ""),
            database=os.getenv("MYSQL_DATABASE", "astron_claw"),
        ),
        redis=RedisConfig(
            host=os.getenv("REDIS_HOST", "127.0.0.1"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD", ""),
            db=int(os.getenv("REDIS_DB", "0")),
            cluster=os.getenv("REDIS_CLUSTER", "false").lower() == "true",
        ),
        server=ServerConfig(
            host=os.getenv("SERVER_HOST", "0.0.0.0"),
            port=int(os.getenv("SERVER_PORT", "8765")),
            workers=int(os.getenv("SERVER_WORKERS", str((os.cpu_count() or 1) + 1))),
            log_level=os.getenv("SERVER_LOG_LEVEL", "info"),
            access_log=os.getenv("SERVER_ACCESS_LOG", "true").lower() == "true",
        ),
        queue=QueueConfig(
            type=os.getenv("QUEUE_TYPE", "redis_stream"),
            max_stream_len=int(os.getenv("QUEUE_MAX_STREAM_LEN", "1000")),
            block_ms=int(os.getenv("QUEUE_BLOCK_MS", "5000")),
        ),
        storage=StorageConfig(
            type=_validate_oss_type(os.getenv("OSS_TYPE", "s3")),
            endpoint=os.getenv("OSS_ENDPOINT", "http://localhost:9000"),
            public_endpoint=(
                os.getenv("OSS_PUBLIC_ENDPOINT")
                or os.getenv("OSS_ENDPOINT", "http://localhost:9000")
            ),
            access_key=os.getenv("OSS_ACCESS_KEY", "minioadmin"),
            secret_key=os.getenv("OSS_SECRET_KEY", "minioadmin"),
            bucket=os.getenv("OSS_BUCKET", "astron-claw-media"),
            region=os.getenv("OSS_REGION", "us-east-1"),
            ttl=int(os.getenv("OSS_TTL", "157788000")),
        ),
    )
