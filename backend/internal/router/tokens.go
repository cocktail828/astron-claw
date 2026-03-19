package router

import (
	"github.com/gin-gonic/gin"
	"github.com/redis/go-redis/v9"
	"github.com/rs/zerolog/log"

	"github.com/hygao1024/astron-claw/backend/internal/pkg"
)

var rateLimitScript = redis.NewScript(`
local count = redis.call("INCR", KEYS[1])
if count == 1 then
    redis.call("EXPIRE", KEYS[1], ARGV[1])
end
return count
`)

func (app *App) createToken(c *gin.Context) {
	// Rate limit: 10 requests per minute per IP
	ip := c.ClientIP()
	rateKey := "rate:create_token:" + ip
	count, err := rateLimitScript.Run(c.Request.Context(), app.RDB, []string{rateKey}, 60).Int64()
	if err != nil {
		log.Error().Err(err).Msg("Rate limit check failed")
		c.JSON(500, gin.H{"code": 500, "error": "Internal server error"})
		return
	}
	if count > 10 {
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
	ip := c.ClientIP()
	rateKey := "rate:validate_token:" + ip
	count, err := rateLimitScript.Run(c.Request.Context(), app.RDB, []string{rateKey}, 60).Int64()
	if err != nil {
		log.Error().Err(err).Msg("Rate limit check failed")
		c.JSON(500, gin.H{"code": 500, "error": "Internal server error"})
		return
	}
	if count > 20 {
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
