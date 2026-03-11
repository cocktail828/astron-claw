"""Prometheus exposition format renderer — read metrics from Redis and format.

Responsibilities:
1. `.` → `_` name conversion
2. Type suffix: counter → `_total`, histogram unit=s → `_seconds`
3. `# HELP` / `# TYPE` from {otlp}:meta
4. Inject `service` label from {otlp}:resource
5. Histogram expansion: `_bucket{le=...}`, `_sum`, `_count`
   - Buckets are converted from per-bucket delta counts to cumulative counts
6. Gauge aggregation: SMEMBERS → HGETALL per worker → SUM, lazy SREM
7. Label value escaping per Prometheus spec
8. Content-Type: text/plain; version=0.0.4; charset=utf-8
"""

from __future__ import annotations

import json
from collections import defaultdict

from redis.asyncio import Redis, RedisCluster

from infra.telemetry.redis_exporter import (
    KEY_COUNTERS,
    KEY_HISTOGRAMS,
    KEY_GAUGE_PIDS,
    KEY_META,
    KEY_RESOURCE,
    _gauge_key,
)


def _escape_label_value(v: str) -> str:
    """Escape label value per Prometheus exposition spec."""
    return v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _prom_name(otel_name: str) -> str:
    """Convert OTel dot-separated name to Prometheus underscore-separated."""
    return otel_name.replace(".", "_")


def _format_labels(labels: dict[str, str]) -> str:
    """Format label dict to Prometheus label string: {k1="v1",k2="v2"}"""
    if not labels:
        return ""
    parts = ",".join(
        f'{k}="{_escape_label_value(v)}"' for k, v in sorted(labels.items())
    )
    return "{" + parts + "}"


def _format_value(v: float) -> str:
    """Format a numeric value, omitting unnecessary decimals for integers."""
    if v != v:  # NaN check (NaN != NaN)
        return "NaN"
    if v == float("inf"):
        return "+Inf"
    if v == float("-inf"):
        return "-Inf"
    if v == int(v):
        return str(int(v))
    return str(v)


def _parse_field(field: str) -> tuple[str, dict] | None:
    """Parse a Redis field key: 'metric_name|{attrs_json}' → (name, attrs_dict).

    Returns None if the field is malformed.
    """
    try:
        sep = field.index("|")
        name = field[:sep]
        attrs = json.loads(field[sep + 1:])
        return name, attrs
    except (ValueError, json.JSONDecodeError):
        return None


async def render_prometheus_exposition(redis: Redis | RedisCluster) -> str:
    """Read all metrics from Redis and render Prometheus exposition text."""
    lines: list[str] = []

    # ── Load resource info ──
    service_name = await redis.hget(KEY_RESOURCE, "service.name") or ""

    # ── Load metadata ──
    meta_raw: dict = await redis.hgetall(KEY_META) or {}
    meta: dict[str, dict] = {}
    for k, v in meta_raw.items():
        try:
            meta[k] = json.loads(v)
        except (json.JSONDecodeError, TypeError):
            pass

    # ── Counters ──
    counters_raw: dict = await redis.hgetall(KEY_COUNTERS) or {}
    # Group by metric name
    counter_groups: dict[str, list[tuple[dict, float]]] = defaultdict(list)
    for field, val in counters_raw.items():
        parsed = _parse_field(field)
        if not parsed:
            continue
        name, attrs = parsed
        counter_groups[name].append((attrs, float(val)))

    for otel_name in sorted(counter_groups):
        pname = _prom_name(otel_name) + "_total"
        m = meta.get(otel_name, {})
        lines.append(f"# HELP {pname} {m.get('description', '')}")
        lines.append(f"# TYPE {pname} counter")
        for attrs, value in counter_groups[otel_name]:
            labels = _inject_service(attrs, service_name)
            lines.append(f"{pname}{_format_labels(labels)} {_format_value(value)}")
        lines.append("")

    # ── Histograms ──
    hist_raw: dict = await redis.hgetall(KEY_HISTOGRAMS) or {}
    # Parse into structure: {metric_name: {attrs_json: {suffix: value}}}
    hist_data: dict[str, dict[str, dict[str, float]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    for field, val in hist_raw.items():
        # field = "metric_name|{attrs_json}|suffix"
        # Use rsplit to split from the RIGHT — suffix (count/sum/bucket_*) never
        # contains "|", so this is safe even if attrs_json contains pipe chars.
        parts = field.rsplit("|", 1)
        if len(parts) != 2:
            continue
        base_key, suffix = parts
        # base_key = "metric_name|{attrs_json}"
        parsed = _parse_field(base_key)
        if not parsed:
            continue
        name, attrs = parsed
        attrs_json = json.dumps(dict(sorted(attrs.items())), ensure_ascii=False)
        hist_data[name][attrs_json][suffix] = float(val)

    for otel_name in sorted(hist_data):
        m = meta.get(otel_name, {})
        unit = m.get("unit", "")
        pname = _prom_name(otel_name)
        if unit == "s":
            pname += "_seconds"

        lines.append(f"# HELP {pname} {m.get('description', '')}")
        lines.append(f"# TYPE {pname} histogram")

        for attrs_json in sorted(hist_data[otel_name]):
            attrs = json.loads(attrs_json)
            labels = _inject_service(attrs, service_name)
            buckets = hist_data[otel_name][attrs_json]

            count = buckets.get("count", 0)
            total_sum = buckets.get("sum", 0)

            # Collect bucket boundaries and sort numerically
            bucket_bounds: list[tuple[float | str, float]] = []
            for k, v in buckets.items():
                if k.startswith("bucket_"):
                    bound_str = k[7:]  # strip "bucket_"
                    if bound_str == "+Inf":
                        bucket_bounds.append((float("inf"), v))
                    else:
                        bucket_bounds.append((float(bound_str), v))

            bucket_bounds.sort(key=lambda x: x[0])

            # Convert from per-bucket (delta) counts to cumulative counts
            cumulative = 0.0
            for bound, delta_count in bucket_bounds:
                cumulative += delta_count
                le_str = "+Inf" if bound == float("inf") else _format_value(bound)
                blabels = {**labels, "le": le_str}
                lines.append(
                    f"{pname}_bucket{_format_labels(blabels)} {_format_value(cumulative)}"
                )

            lines.append(f"{pname}_sum{_format_labels(labels)} {_format_value(total_sum)}")
            lines.append(f"{pname}_count{_format_labels(labels)} {_format_value(count)}")
        lines.append("")

    # ── Gauges (UpDownCounter — per-worker aggregation) ──
    gauge_pids: set = await redis.smembers(KEY_GAUGE_PIDS) or set()
    # Aggregate across all alive workers
    gauge_agg: dict[str, float] = defaultdict(float)
    dead_pids: list[str] = []

    for pid in gauge_pids:
        gk = _gauge_key(pid)
        fields: dict = await redis.hgetall(gk) or {}
        if not fields:
            # Key expired — worker crashed; lazy cleanup
            dead_pids.append(pid)
            continue
        for field, val in fields.items():
            gauge_agg[field] += float(val)

    # Lazy SREM dead PIDs
    if dead_pids:
        await redis.srem(KEY_GAUGE_PIDS, *dead_pids)

    # Group by metric name
    gauge_groups: dict[str, list[tuple[dict, float]]] = defaultdict(list)
    for field, total in gauge_agg.items():
        parsed = _parse_field(field)
        if not parsed:
            continue
        name, attrs = parsed
        gauge_groups[name].append((attrs, total))

    for otel_name in sorted(gauge_groups):
        pname = _prom_name(otel_name)
        m = meta.get(otel_name, {})
        lines.append(f"# HELP {pname} {m.get('description', '')}")
        lines.append(f"# TYPE {pname} gauge")
        for attrs, value in gauge_groups[otel_name]:
            labels = _inject_service(attrs, service_name)
            lines.append(f"{pname}{_format_labels(labels)} {_format_value(value)}")
        lines.append("")

    return "\n".join(lines)


async def reset_all_metrics(redis: Redis | RedisCluster) -> None:
    """Delete all OTLP metric keys from Redis."""
    keys_to_del = [KEY_COUNTERS, KEY_HISTOGRAMS, KEY_META, KEY_RESOURCE]

    # Collect gauge per-worker keys
    gauge_pids: set = await redis.smembers(KEY_GAUGE_PIDS) or set()
    for pid in gauge_pids:
        keys_to_del.append(_gauge_key(pid))
    keys_to_del.append(KEY_GAUGE_PIDS)

    if keys_to_del:
        await redis.delete(*keys_to_del)


def _inject_service(attrs: dict, service_name: str) -> dict[str, str]:
    """Return a new label dict with service injected as the first key."""
    labels: dict[str, str] = {}
    if service_name:
        labels["service"] = service_name
    labels.update({k: str(v) for k, v in attrs.items()})
    return labels
