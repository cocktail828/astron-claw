package storage

import (
	"io"

	"github.com/rs/zerolog/log"

	"astron-claw/backend/internal/config"
)

// ObjectStorage is the unified interface for object storage providers.
type ObjectStorage interface {
	Start() error
	Close() error
	EnsureBucket() error
	PutObject(key string, body io.Reader, contentType string, contentLength int64) (string, error)
	BucketName() string
}

// NewStorage creates a storage backend based on config type.
func NewStorage(cfg config.StorageConfig) ObjectStorage {
	switch cfg.Type {
	case "s3":
		log.Info().Str("type", cfg.Type).Str("endpoint", cfg.Endpoint).Msg("Storage backend")
		return NewS3Storage(cfg)
	case "ifly_gateway":
		log.Info().Str("type", cfg.Type).Str("endpoint", cfg.Endpoint).Msg("Storage backend")
		return NewIFlyGatewayStorage(cfg)
	default:
		log.Fatal().Str("type", cfg.Type).Msg("Unknown storage type")
		return nil
	}
}
