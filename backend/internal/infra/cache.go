package infra

import (
	"context"
	"fmt"
	"strconv"
	"strings"
	"time"

	"github.com/redis/go-redis/v9"
	"github.com/rs/zerolog/log"

	"github.com/hygao1024/astron-claw/backend/internal/config"
)

func InitRedis(cfg config.RedisConfig) (redis.UniversalClient, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	var rdb redis.UniversalClient

	if cfg.IsCluster() {
		rdb = redis.NewClusterClient(&redis.ClusterOptions{
			Addrs:    cfg.Addrs,
			Password: cfg.Password,
		})
	} else {
		addr := cfg.Host + ":" + strconv.Itoa(cfg.Port)
		if len(cfg.Addrs) == 1 {
			addr = cfg.Addrs[0]
		}
		rdb = redis.NewClient(&redis.Options{
			Addr:     addr,
			Password: cfg.Password,
			DB:       cfg.DB,
		})
	}

	if err := rdb.Ping(ctx).Err(); err != nil {
		return nil, fmt.Errorf("redis ping: %w", err)
	}

	mode := "standalone"
	addr := cfg.Host + ":" + strconv.Itoa(cfg.Port)
	if cfg.IsCluster() {
		mode = "cluster"
		addr = strings.Join(cfg.Addrs, ",")
	} else if len(cfg.Addrs) == 1 {
		addr = cfg.Addrs[0]
	}

	log.Info().
		Str("mode", mode).
		Str("addr", addr).
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
