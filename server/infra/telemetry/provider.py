"""Telemetry provider — initialise / shutdown OTel MeterProvider."""

from __future__ import annotations

from typing import TYPE_CHECKING

from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.metrics.view import View, ExplicitBucketHistogramAggregation
from opentelemetry.sdk.resources import Resource
from opentelemetry.metrics import set_meter_provider

from infra.telemetry.config import OtlpConfig
from infra.telemetry.redis_exporter import RedisMetricExporter
from infra.telemetry.metrics import REQUEST_DURATION_BUCKETS, STREAM_DURATION_BUCKETS
from infra.log import logger

if TYPE_CHECKING:
    from infra.config import RedisConfig

_provider: MeterProvider | None = None


async def init_telemetry(
    config: OtlpConfig,
    redis_config: RedisConfig,
) -> None:
    """Initialise OTel MeterProvider + RedisMetricExporter.

    If ``config.enabled`` is False, does nothing — the OTel API automatically
    falls back to NoOp instruments.
    """
    global _provider

    if not config.enabled:
        logger.info("OTLP telemetry disabled (OTLP_ENABLED=false)")
        return

    if not config.metrics_enabled:
        logger.info("OTLP metrics disabled")
        return

    exporter = RedisMetricExporter(
        host=redis_config.host,
        port=redis_config.port,
        password=redis_config.password,
        db=redis_config.db,
        cluster=redis_config.cluster,
        service_name=config.service_name,
        export_interval_ms=config.export_interval_ms,
    )

    # Use the exporter's preferred temporality mapping
    reader = PeriodicExportingMetricReader(
        exporter,
        export_interval_millis=config.export_interval_ms,
        export_timeout_millis=config.export_interval_ms,
    )

    resource = Resource.create({"service.name": config.service_name})

    # Custom bucket boundaries for histograms
    views = [
        View(
            instrument_name="bridge.chat.request.duration",
            aggregation=ExplicitBucketHistogramAggregation(
                boundaries=list(REQUEST_DURATION_BUCKETS),
            ),
        ),
        View(
            instrument_name="bridge.chat.stream.duration",
            aggregation=ExplicitBucketHistogramAggregation(
                boundaries=list(STREAM_DURATION_BUCKETS),
            ),
        ),
    ]

    _provider = MeterProvider(
        resource=resource, metric_readers=[reader], views=views,
    )
    set_meter_provider(_provider)

    logger.info(
        "OTLP telemetry initialised (service={}, interval={}ms)",
        config.service_name,
        config.export_interval_ms,
    )


async def shutdown_telemetry() -> None:
    """Gracefully shut down all Providers — flushes buffered metrics to Redis."""
    global _provider
    if _provider is not None:
        _provider.shutdown()
        logger.info("OTLP telemetry shut down")
        _provider = None
