"""Predefined OTel metric instruments for /bridge/chat.

Instruments are created at module import time using the current MeterProvider
(initially NoOp). After ``init_telemetry()`` installs the real provider,
``ensure_instruments()`` re-creates them so they record to Redis.

When OTLP is disabled the provider stays NoOp, so ``.add()`` / ``.record()``
calls are zero-overhead no-ops.
"""

from __future__ import annotations

from opentelemetry.metrics import (
    Counter,
    Histogram,
    UpDownCounter,
    Meter,
    get_meter,
)

# Histogram bucket boundaries
REQUEST_DURATION_BUCKETS = (
    0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0,
)
STREAM_DURATION_BUCKETS = (1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0)


def _create_instruments(meter: Meter) -> tuple[Counter, Histogram, Histogram, UpDownCounter]:
    return (
        meter.create_counter(
            name="bridge.chat.requests",
            description="/bridge/chat 请求总数",
        ),
        meter.create_histogram(
            name="bridge.chat.request.duration",
            description="/bridge/chat 首字节耗时",
            unit="s",
        ),
        meter.create_histogram(
            name="bridge.chat.stream.duration",
            description="SSE 流持续时长",
            unit="s",
        ),
        meter.create_up_down_counter(
            name="bridge.chat.active_streams",
            description="当前活跃 SSE 流数量",
        ),
    )


# Initialise with NoOp meter (safe at import time)
_meter = get_meter("astron_claw")
chat_request_total, chat_request_duration, chat_stream_duration, chat_active_streams = (
    _create_instruments(_meter)
)


def ensure_instruments() -> None:
    """Re-create instruments using the current (real) MeterProvider.

    Must be called after ``init_telemetry()`` so the real MeterProvider is active.
    Idempotent — safe to call multiple times.
    """
    global chat_request_total, chat_request_duration
    global chat_stream_duration, chat_active_streams

    meter: Meter = get_meter("astron_claw")
    chat_request_total, chat_request_duration, chat_stream_duration, chat_active_streams = (
        _create_instruments(meter)
    )


def _token_prefix(token: str) -> str:
    """Return first 10 chars + '...' for token label."""
    return f"{token[:10]}..." if len(token) > 10 else token
