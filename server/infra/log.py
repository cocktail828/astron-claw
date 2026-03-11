"""Loguru logging configuration.

Import `logger` from this module instead of using stdlib logging.
Call `setup_logging()` once at startup to configure sinks and intercept
third-party libraries that use stdlib logging (uvicorn, sqlalchemy, etc.).
"""

import json
import logging
import os
import sys

from loguru import logger

LOG_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level>"
)

# Plain text format for file sinks (no ANSI colour tags)
_FILE_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | "
    "{level: <8} | "
    "{name}:{function}:{line} - "
    "{message}"
)


def _json_serializer(record):
    """Loguru ``format`` callable — receives a record dict, returns a format template.

    We pre-serialize the JSON and stash it in ``extra["_serialized"]`` so the
    returned template ``{extra[_serialized]}\\n`` lets loguru substitute it
    without conflicting with JSON braces.
    """
    record["extra"]["_serialized"] = json.dumps(
        {
            "timestamp": record["time"].strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "level": record["level"].name,
            "module": record["name"],
            "function": record["function"],
            "line": record["line"],
            "message": record["message"],
            "extra": {
                k: v for k, v in record["extra"].items() if k != "_serialized"
            },
        },
        ensure_ascii=False,
        default=str,
    )
    return "{extra[_serialized]}\n"


class _InterceptHandler(logging.Handler):
    """Redirect stdlib logging records to loguru."""

    def emit(self, record: logging.LogRecord) -> None:
        # Map stdlib level to loguru level
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Find the caller that originated the log call
        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def setup_logging(level: str = "INFO") -> None:
    """Configure loguru as the sole logging backend.

    - Removes default loguru sink and adds a formatted stderr sink.
    - Intercepts stdlib logging so uvicorn / sqlalchemy / alembic logs
      are also routed through loguru.
    - Adds a rotating file sink at ``logs/server.log``.
    - Adds an error-only file sink at ``logs/error.log`` (WARNING+).
    - Supports JSON output via ``LOG_FORMAT_JSON=true``.
    - Log directory configurable via ``LOG_DIR`` env var.
    """
    from pathlib import Path

    log_dir = Path(os.getenv("LOG_DIR", str(Path(__file__).resolve().parent.parent / "logs")))
    log_dir.mkdir(parents=True, exist_ok=True)

    use_json = os.getenv("LOG_FORMAT_JSON", "false").lower() == "true"

    # Reset loguru sinks
    logger.remove()

    # Console sink (stderr)
    if use_json:
        logger.add(sys.stderr, format=_json_serializer, level=level, colorize=False)
    else:
        logger.add(sys.stderr, format=LOG_FORMAT, level=level, colorize=True)

    # Rotating file sink — all levels
    file_fmt = _json_serializer if use_json else _FILE_FORMAT
    logger.add(
        str(log_dir / "server.log"),
        format=file_fmt,
        level=level,
        rotation="50 MB",
        retention="30 days",
        compression="gz",
        encoding="utf-8",
    )

    # Error-only file sink — WARNING and above
    logger.add(
        str(log_dir / "error.log"),
        format=file_fmt,
        level="WARNING",
        rotation="50 MB",
        retention="30 days",
        compression="gz",
        encoding="utf-8",
    )

    # Intercept stdlib logging → loguru
    logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)

    # Intercept uvicorn loggers — clear their handlers and disable propagation
    # so they only go through the root InterceptHandler once.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers.clear()
        uv_logger.propagate = True

    logger.info("Logging initialised: level={}, json={}, dir={}", level, use_json, log_dir)
