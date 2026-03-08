#!/usr/bin/env python3
"""Astron Claw Bridge Server — production entry point.

Uses uvloop (high-performance event loop) + httptools (C-level HTTP parsing)
for maximum throughput. Configuration is loaded from .env.

Multi-worker is supported: bot liveness is tracked in a shared Redis ZSET
(bridge:bot_alive) and cross-worker messaging goes through Redis Streams
(bridge:bot_inbox / bridge:chat_inbox), so each worker only holds its own
WebSocket connections while the cluster behaves as a single logical bridge.
"""

import uvicorn

from infra.log import setup_logging
from infra.config import load_config

config = load_config()
server = config.server

setup_logging(level=server.log_level.upper())

if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host=server.host,
        port=server.port,
        workers=server.workers,
        loop="uvloop",
        http="httptools",
        ws="websockets",
        log_config=None,
        log_level=server.log_level,
        access_log=server.access_log,
        timeout_keep_alive=30,
        timeout_graceful_shutdown=10,
    )
