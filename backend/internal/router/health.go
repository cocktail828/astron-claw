package router

import (
	"context"
	"sync"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/rs/zerolog/log"
)

const (
	defaultHealthCheckTimeout = 1 * time.Second
	healthDeadlineSlack       = 50 * time.Millisecond
)

func newHealthCheckContext(parent context.Context) (context.Context, context.CancelFunc) {
	base := context.Background()
	timeout := defaultHealthCheckTimeout

	if parent != nil {
		base = context.WithoutCancel(parent)
		if deadline, ok := parent.Deadline(); ok {
			remaining := time.Until(deadline) - healthDeadlineSlack
			if remaining > 0 && remaining < timeout {
				timeout = remaining
			}
		}
	}

	return context.WithTimeout(base, timeout)
}

func checkDependencyHealth(parent context.Context, ping func(context.Context) error) bool {
	ctx, cancel := newHealthCheckContext(parent)
	defer cancel()
	return ping(ctx) == nil
}

func (app *App) healthCheck(c *gin.Context) {
	mysqlOK := false
	redisOK := false

	var wg sync.WaitGroup
	wg.Add(2)

	// Check MySQL
	sqlDB, err := app.DB.DB()
	if err != nil {
		log.Warn().Err(err).Msg("MySQL health check init failed")
	} else {
		go func() {
			defer wg.Done()
			mysqlOK = checkDependencyHealth(c.Request.Context(), func(ctx context.Context) error {
				if err := sqlDB.PingContext(ctx); err != nil {
					log.Warn().Err(err).Msg("MySQL health check failed")
					return err
				}
				return nil
			})
		}()
	}

	// Check Redis
	go func() {
		defer wg.Done()
		redisOK = checkDependencyHealth(c.Request.Context(), func(ctx context.Context) error {
			if err := app.RDB.Ping(ctx).Err(); err != nil {
				log.Warn().Err(err).Msg("Redis health check failed")
				return err
			}
			return nil
		})
	}()

	if err != nil {
		wg.Done()
	}

	wg.Wait()

	status := "ok"
	httpCode := 200
	if !mysqlOK || !redisOK {
		status = "degraded"
		httpCode = 503
		log.Warn().Bool("mysql", mysqlOK).Bool("redis", redisOK).Msg("Health check degraded")
	}

	c.JSON(httpCode, gin.H{
		"code":   0,
		"status": status,
		"mysql":  mysqlOK,
		"redis":  redisOK,
	})
}
