package router

import (
	"net/http"

	"github.com/gin-gonic/gin"
	"github.com/gorilla/websocket"
	"github.com/rs/zerolog/log"

	"github.com/hygao1024/astron-claw/backend/internal/model"
	"github.com/hygao1024/astron-claw/backend/internal/service"
)

var wsUpgrader = websocket.Upgrader{
	CheckOrigin: func(r *http.Request) bool { return true },
}

func (app *App) wsBot(c *gin.Context) {
	botToken := c.Query("token")
	if botToken == "" {
		botToken = c.GetHeader("x-astron-bot-token")
	}

	if !app.TokenMgr.Validate(c.Request.Context(), botToken) {
		// Accept then close with error code
		conn, err := wsUpgrader.Upgrade(c.Writer, c.Request, nil)
		if err != nil {
			log.Error().Err(err).Msg("WS upgrade failed for invalid token")
			return
		}
		msg := websocket.FormatCloseMessage(model.ErrWSInvalidToken.Code, model.ErrWSInvalidToken.Message)
		_ = conn.WriteMessage(websocket.CloseMessage, msg)
		conn.Close()
		tp := botToken
		if len(tp) > 10 {
			tp = tp[:10]
		}
		log.Warn().Str("token", tp+"...").Msg("Bot connection rejected: invalid token")
		return
	}

	conn, err := wsUpgrader.Upgrade(c.Writer, c.Request, nil)
	if err != nil {
		log.Error().Err(err).Msg("WS upgrade failed")
		return
	}

	clientAddr := c.ClientIP()
	botConn := &service.BotConn{
		Conn:  conn,
		Token: botToken,
	}

	ctx := c.Request.Context()
	app.Bridge.RegisterBot(ctx, botToken, botConn)

	tp := botToken[:10]
	log.Info().Str("token", tp+"...").Str("from", clientAddr).Msg("Bot connected")
	app.Bridge.NotifyBotConnected(botToken)

	defer func() {
		app.Bridge.UnregisterBot(ctx, botToken, botConn)
	}()

	for {
		_, message, err := conn.ReadMessage()
		if err != nil {
			if websocket.IsCloseError(err, websocket.CloseNormalClosure, websocket.CloseGoingAway) {
				log.Info().Str("token", tp+"...").Str("from", clientAddr).Msg("Bot disconnected normally")
			} else if websocket.IsUnexpectedCloseError(err, websocket.CloseNormalClosure, websocket.CloseGoingAway) {
				log.Info().Str("token", tp+"...").Str("from", clientAddr).Err(err).Msg("Bot disconnected unexpectedly")
			} else {
				log.Error().Err(err).Str("token", tp+"...").Msg("Bot connection error")
			}
			return
		}

		app.Bridge.HandleBotMessage(ctx, botToken, string(message))
	}
}
