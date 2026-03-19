package telemetry

import (
	"context"
	"time"

	"github.com/redis/go-redis/v9"
	"github.com/rs/zerolog/log"
	"go.opentelemetry.io/otel"
	sdkmetric "go.opentelemetry.io/otel/sdk/metric"
	"go.opentelemetry.io/otel/sdk/resource"
	semconv "go.opentelemetry.io/otel/semconv/v1.26.0"

	"github.com/hygao1024/astron-claw/backend/internal/config"
)

var provider *sdkmetric.MeterProvider

// Init initializes OTel MeterProvider with RedisMetricExporter.
func Init(otlpCfg config.OtlpConfig, rdb redis.UniversalClient) error {
	if !otlpCfg.Enabled {
		log.Info().Msg("OTLP telemetry disabled (OTLP_ENABLED=false)")
		return nil
	}

	if !otlpCfg.MetricsEnabled {
		log.Info().Msg("OTLP metrics disabled")
		return nil
	}

	exporter := NewRedisMetricExporter(
		rdb,
		otlpCfg.ServiceName,
		otlpCfg.ExportIntervalMs,
	)

	reader := sdkmetric.NewPeriodicReader(
		exporter,
		sdkmetric.WithInterval(
			time.Duration(otlpCfg.ExportIntervalMs)*time.Millisecond,
		),
	)

	res := resource.NewWithAttributes(
		semconv.SchemaURL,
		semconv.ServiceNameKey.String(otlpCfg.ServiceName),
	)

	// Custom bucket boundaries
	requestDurationView := sdkmetric.NewView(
		sdkmetric.Instrument{Name: "bridge.chat.request.duration"},
		sdkmetric.Stream{
			Aggregation: sdkmetric.AggregationExplicitBucketHistogram{
				Boundaries: RequestDurationBuckets,
			},
		},
	)
	streamDurationView := sdkmetric.NewView(
		sdkmetric.Instrument{Name: "bridge.chat.stream.duration"},
		sdkmetric.Stream{
			Aggregation: sdkmetric.AggregationExplicitBucketHistogram{
				Boundaries: StreamDurationBuckets,
			},
		},
	)

	provider = sdkmetric.NewMeterProvider(
		sdkmetric.WithResource(res),
		sdkmetric.WithReader(reader),
		sdkmetric.WithView(requestDurationView, streamDurationView),
	)
	otel.SetMeterProvider(provider)

	log.Info().
		Str("service", otlpCfg.ServiceName).
		Int("interval_ms", otlpCfg.ExportIntervalMs).
		Msg("OTLP telemetry initialised")

	return nil
}

// Shutdown gracefully shuts down all providers.
func Shutdown() {
	if provider != nil {
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		if err := provider.Shutdown(ctx); err != nil {
			log.Error().Err(err).Msg("OTLP telemetry shutdown error")
		}
		log.Info().Msg("OTLP telemetry shut down")
		provider = nil
	}
}
