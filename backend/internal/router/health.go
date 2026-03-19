package router

import (
	"github.com/gin-gonic/gin"
	"github.com/rs/zerolog/log"
)

func (app *App) healthCheck(c *gin.Context) {
	mysqlOK := false
	redisOK := false

	// Check MySQL
	sqlDB, err := app.DB.DB()
	if err == nil {
		if err := sqlDB.PingContext(c.Request.Context()); err == nil {
			mysqlOK = true
		} else {
			log.Warn().Err(err).Msg("MySQL health check failed")
		}
	}

	// Check Redis
	if err := app.RDB.Ping(c.Request.Context()).Err(); err == nil {
		redisOK = true
	} else {
		log.Warn().Err(err).Msg("Redis health check failed")
	}

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
