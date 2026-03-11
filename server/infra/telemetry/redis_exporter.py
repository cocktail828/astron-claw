"""RedisMetricExporter — write OTel metrics to Redis.

- Counter / Histogram (delta temporality): HINCRBYFLOAT to shared Hash keys.
- UpDownCounter / Gauge (cumulative temporality): HSET to per-worker Hash + TTL.

Uses a **synchronous** ``redis.Redis`` client because the OTel SDK calls
``export()`` from a background thread — ``redis.asyncio`` connections are
bound to the event loop they were created in and cannot be reused across loops.
"""

from __future__ import annotations

import json
import os

from opentelemetry.sdk.metrics.export import (
    AggregationTemporality,
    Histogram as HistogramPoint,
    MetricExporter,
    MetricExportResult,
    MetricsData,
    Sum,
)
import redis as sync_redis

from infra.log import logger

# Redis key constants (using {otlp} hash tag for Cluster slot co-location)
KEY_COUNTERS = "{otlp}:counters"
KEY_HISTOGRAMS = "{otlp}:histograms"
KEY_GAUGE_PIDS = "{otlp}:gauge_pids"
KEY_META = "{otlp}:meta"
KEY_RESOURCE = "{otlp}:resource"


def _gauge_key(pid: int | str) -> str:
    return f"{{otlp}}:gauges:{pid}"


def _attrs_key(name: str, attrs: dict) -> str:
    """Build a field key: metric_name|sorted_attrs_json"""
    sorted_attrs = json.dumps(dict(sorted(attrs.items())), ensure_ascii=False)
    return f"{name}|{sorted_attrs}"


class RedisMetricExporter(MetricExporter):
    """Export OTel metrics to Redis for multi-worker aggregation.

    Counter/Histogram: delta temporality → HINCRBYFLOAT on shared keys.
    UpDownCounter (Gauge): cumulative temporality → SET on per-worker keys + TTL.

    Internally creates a **synchronous** Redis client so that ``export()``
    (called from the OTel background thread) works correctly.
    """

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 6379,
        password: str = "",
        db: int = 0,
        cluster: bool = False,
        service_name: str = "astron-claw",
        export_interval_ms: int = 10_000,
    ):
        from opentelemetry.sdk.metrics import Counter, Histogram, UpDownCounter

        super().__init__(
            preferred_temporality={
                Counter: AggregationTemporality.DELTA,
                Histogram: AggregationTemporality.DELTA,
                UpDownCounter: AggregationTemporality.CUMULATIVE,
            },
        )

        # Create a synchronous Redis client for background-thread export
        if cluster:
            self._redis: sync_redis.Redis | sync_redis.RedisCluster = (
                sync_redis.RedisCluster(
                    host=host,
                    port=port,
                    password=password or None,
                    decode_responses=True,
                )
            )
        else:
            self._redis = sync_redis.Redis(
                host=host,
                port=port,
                password=password or None,
                db=db,
                decode_responses=True,
            )

        self._pid = os.getpid()
        self._service_name = service_name
        self._gauge_ttl_ms = export_interval_ms * 3

    def export(
        self,
        metrics_data: MetricsData,
        timeout_millis: float = 10_000,
        **kwargs,
    ) -> MetricExportResult:
        """Synchronous export — called from OTel background thread."""
        try:
            self._do_export(metrics_data)
            return MetricExportResult.SUCCESS
        except Exception:
            logger.opt(exception=True).warning("RedisMetricExporter: export failed")
            return MetricExportResult.FAILURE

    def _do_export(self, metrics_data: MetricsData) -> None:
        pipe = self._redis.pipeline(transaction=False)
        gauge_fields: dict[str, str] = {}
        has_gauge = False

        for resource_metric in metrics_data.resource_metrics:
            for scope_metric in resource_metric.scope_metrics:
                for metric in scope_metric.metrics:
                    name = metric.name
                    data = metric.data

                    # Store metadata for reader
                    meta = self._build_meta(metric)
                    if meta:
                        pipe.hset(KEY_META, name, json.dumps(meta, ensure_ascii=False))

                    if isinstance(data, Sum):
                        if data.is_monotonic:
                            # Counter — delta → HINCRBYFLOAT
                            for dp in data.data_points:
                                attrs = dict(dp.attributes) if dp.attributes else {}
                                field = _attrs_key(name, attrs)
                                pipe.hincrbyfloat(KEY_COUNTERS, field, dp.value)
                        else:
                            # UpDownCounter — cumulative → per-worker SET
                            has_gauge = True
                            for dp in data.data_points:
                                attrs = dict(dp.attributes) if dp.attributes else {}
                                field = _attrs_key(name, attrs)
                                gauge_fields[field] = str(dp.value)

                    elif isinstance(data, HistogramPoint):
                        # Histogram — delta → HINCRBYFLOAT for count, sum, buckets
                        for dp in data.data_points:
                            attrs = dict(dp.attributes) if dp.attributes else {}
                            base = _attrs_key(name, attrs)
                            pipe.hincrbyfloat(
                                KEY_HISTOGRAMS, f"{base}|count", dp.count
                            )
                            pipe.hincrbyfloat(
                                KEY_HISTOGRAMS, f"{base}|sum", dp.sum
                            )
                            # Explicit bucket boundaries
                            for i, bound in enumerate(dp.explicit_bounds):
                                bucket_count = dp.bucket_counts[i]
                                pipe.hincrbyfloat(
                                    KEY_HISTOGRAMS,
                                    f"{base}|bucket_{bound}",
                                    bucket_count,
                                )
                            # +Inf bucket
                            pipe.hincrbyfloat(
                                KEY_HISTOGRAMS,
                                f"{base}|bucket_+Inf",
                                dp.bucket_counts[-1],
                            )

        # Write gauge data to per-worker key
        if has_gauge and gauge_fields:
            gk = _gauge_key(self._pid)
            pipe.hset(gk, mapping=gauge_fields)
            pipe.pexpire(gk, self._gauge_ttl_ms)
            pipe.sadd(KEY_GAUGE_PIDS, str(self._pid))

        # Write resource info (idempotent)
        pipe.hset(KEY_RESOURCE, "service.name", self._service_name)

        pipe.execute()

    @staticmethod
    def _build_meta(metric) -> dict | None:
        data = metric.data
        if isinstance(data, Sum):
            mtype = "counter" if data.is_monotonic else "up_down_counter"
        elif isinstance(data, HistogramPoint):
            mtype = "histogram"
        else:
            return None
        return {
            "type": mtype,
            "description": metric.description or "",
            "unit": metric.unit or "",
        }

    def shutdown(self, timeout_millis: float = 30_000, **kwargs) -> None:
        self.force_flush(timeout_millis=timeout_millis)
        self._redis.close()

    def force_flush(self, timeout_millis: float = 10_000, **kwargs) -> bool:
        return True
