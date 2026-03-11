from dataclasses import dataclass


@dataclass(frozen=True)
class OtlpConfig:
    enabled: bool
    service_name: str
    export_interval_ms: int
    metrics_enabled: bool
    traces_enabled: bool
    logs_enabled: bool
