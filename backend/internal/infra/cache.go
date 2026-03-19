package infra

import (
	"context"
	"fmt"
	"strconv"
	"time"

	"github.com/redis/go-redis/v9"
	"github.com/rs/zerolog/log"

	"github.com/hygao1024/astron-claw/backend/internal/config"
)

func InitRedis(cfg config.RedisConfig) (redis.UniversalClient, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	var rdb redis.UniversalClient

	if cfg.Cluster {
		rdb = redis.NewClusterClient(&redis.ClusterOptions{
			Addrs:    []string{cfg.Host + ":" + strconv.Itoa(cfg.Port)},
			Password: cfg.Password,
		})
	} else {
		rdb = redis.NewClient(&redis.Options{
			Addr:     cfg.Host + ":" + strconv.Itoa(cfg.Port),
			Password: cfg.Password,
			DB:       cfg.DB,
		})
	}

	if err := rdb.Ping(ctx).Err(); err != nil {
		return nil, fmt.Errorf("redis ping: %w", err)
	}

	mode := "standalone"
	if cfg.Cluster {
		mode = "cluster"
	}

	log.Info().
		Str("mode", mode).
		Str("addr", cfg.Host+":"+strconv.Itoa(cfg.Port)).
		Msg("Redis connected")

	return rdb, nil
}

func CloseRedis(rdb redis.UniversalClient) {
	if rdb == nil {
		return
	}
	if err := rdb.Close(); err != nil {
		log.Error().Err(err).Msg("Failed to close Redis")
	} else {
		log.Info().Msg("Redis connection closed")
	}
}
