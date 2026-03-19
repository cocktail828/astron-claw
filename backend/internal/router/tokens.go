package router

import (
	"time"

	"github.com/gin-gonic/gin"
	"github.com/rs/zerolog/log"

	"astron-claw/backend/internal/pkg"
)

// checkRateLimit increments a per-key counter and returns true if the limit is exceeded.
// Uses plain INCR + EXPIRE instead of Lua scripts for Redis Cluster compatibility.
func (app *App) checkRateLimit(c *gin.Context, key string, window time.Duration, limit int64) bool {
	ctx := c.Request.Context()
	count, err := app.RDB.Incr(ctx, key).Result()
	if err != nil {
		log.Warn().Err(err).Str("key", key).Msg("Rate limit INCR failed, allowing request")
		return false
	}
	if count == 1 {
		app.RDB.Expire(ctx, key, window)
	}
	return count > limit
}

func (app *App) createToken(c *gin.Context) {
	// Rate limit: 10 requests per minute per IP
	if app.checkRateLimit(c, "rate:create_token:"+c.ClientIP(), 60*time.Second, 10) {
		c.JSON(429, gin.H{"code": 429, "error": "Too many requests. Please try again later."})
		return
	}

	token, err := app.TokenMgr.Generate(c.Request.Context(), "", 0)
	if err != nil {
		log.Error().Err(err).Msg("Failed to generate token")
		c.JSON(500, gin.H{"code": 500, "error": "Internal server error"})
		return
	}
	log.Info().Str("token", pkg.SafePrefix(token, 10)).Msg("Token created via public API")
	c.JSON(200, gin.H{"code": 0, "token": token})
}

func (app *App) validateToken(c *gin.Context) {
	// Rate limit: 20 requests per minute per IP
	if app.checkRateLimit(c, "rate:validate_token:"+c.ClientIP(), 60*time.Second, 20) {
		c.JSON(429, gin.H{"code": 429, "error": "Too many requests. Please try again later."})
		return
	}

	var body struct {
		Token string `json:"token"`
	}
	if err := c.ShouldBindJSON(&body); err != nil {
		c.JSON(400, gin.H{"code": 400, "error": "Invalid request"})
		return
	}

	valid := app.TokenMgr.Validate(c.Request.Context(), body.Token)
	botConnected := false
	if valid {
		botConnected = app.Bridge.IsBotConnected(c.Request.Context(), body.Token)
	}

	tokenPrefix := pkg.SafePrefix(body.Token, 10)
	log.Debug().Str("token", tokenPrefix).Bool("valid", valid).Msg("Token validate")

	c.JSON(200, gin.H{
		"code":          0,
		"valid":         valid,
		"bot_connected": botConnected,
	})
}
