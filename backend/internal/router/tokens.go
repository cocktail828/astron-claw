package router

import (
	"github.com/gin-gonic/gin"
	"github.com/rs/zerolog/log"
)

func (app *App) createToken(c *gin.Context) {
	token, err := app.TokenMgr.Generate(c.Request.Context(), "", 0)
	if err != nil {
		c.JSON(500, gin.H{"code": 500, "error": err.Error()})
		return
	}
	log.Info().Str("token", token[:10]+"...").Msg("Token created via public API")
	c.JSON(200, gin.H{"code": 0, "token": token})
}

func (app *App) validateToken(c *gin.Context) {
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

	tokenPrefix := "?"
	if len(body.Token) >= 10 {
		tokenPrefix = body.Token[:10]
	}
	log.Debug().Str("token", tokenPrefix+"...").Bool("valid", valid).Msg("Token validate")

	c.JSON(200, gin.H{
		"code":          0,
		"valid":         valid,
		"bot_connected": botConnected,
	})
}
