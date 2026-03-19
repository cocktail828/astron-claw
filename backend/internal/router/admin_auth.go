package router

import (
	"net/http"

	"github.com/gin-gonic/gin"
	"github.com/rs/zerolog/log"

	"github.com/hygao1024/astron-claw/backend/internal/model"
)

func (app *App) adminAuthStatus(c *gin.Context) {
	ctx := c.Request.Context()
	adminSession, _ := c.Cookie("admin_session")

	needSetup := true
	isSet, err := app.AdminAuth.IsPasswordSet(ctx)
	if err == nil {
		needSetup = !isSet
	}

	authenticated := app.AdminAuth.ValidateSession(ctx, adminSession)

	c.JSON(200, gin.H{
		"code":          0,
		"need_setup":    needSetup,
		"authenticated": authenticated,
	})
}

func (app *App) adminAuthSetup(c *gin.Context) {
	ctx := c.Request.Context()

	isSet, _ := app.AdminAuth.IsPasswordSet(ctx)
	if isSet {
		model.ErrorResponse(c, model.ErrAdminPasswordExists)
		return
	}

	var body struct {
		Password string `json:"password"`
	}
	if err := c.ShouldBindJSON(&body); err != nil {
		c.JSON(400, gin.H{"code": 400, "error": "Invalid request"})
		return
	}
	if len(body.Password) < 4 {
		model.ErrorResponse(c, model.ErrAdminPasswordShort)
		return
	}

	if err := app.AdminAuth.SetPassword(ctx, body.Password); err != nil {
		c.JSON(500, gin.H{"code": 500, "error": err.Error()})
		return
	}

	session, err := app.AdminAuth.CreateSession(ctx)
	if err != nil {
		c.JSON(500, gin.H{"code": 500, "error": err.Error()})
		return
	}

	log.Info().Msg("Admin password set up for the first time")
	c.SetSameSite(http.SameSiteLaxMode)
	c.SetCookie("admin_session", session, 86400, "/", "", false, true)
	c.JSON(200, gin.H{"code": 0})
}

func (app *App) adminAuthLogin(c *gin.Context) {
	ctx := c.Request.Context()

	var body struct {
		Password string `json:"password"`
	}
	if err := c.ShouldBindJSON(&body); err != nil {
		c.JSON(400, gin.H{"code": 400, "error": "Invalid request"})
		return
	}

	valid, err := app.AdminAuth.VerifyPassword(ctx, body.Password)
	if err != nil {
		c.JSON(500, gin.H{"code": 500, "error": err.Error()})
		return
	}
	if !valid {
		log.Warn().Msg("Admin login failed — wrong password")
		model.ErrorResponse(c, model.ErrAuthWrongPassword)
		return
	}

	session, err := app.AdminAuth.CreateSession(ctx)
	if err != nil {
		c.JSON(500, gin.H{"code": 500, "error": err.Error()})
		return
	}

	log.Info().Msg("Admin logged in successfully")
	c.SetSameSite(http.SameSiteLaxMode)
	c.SetCookie("admin_session", session, 86400, "/", "", false, true)
	c.JSON(200, gin.H{"code": 0})
}

func (app *App) adminAuthLogout(c *gin.Context) {
	adminSession, _ := c.Cookie("admin_session")
	app.AdminAuth.RemoveSession(c.Request.Context(), adminSession)
	log.Info().Msg("Admin logged out")
	c.SetSameSite(http.SameSiteLaxMode)
	c.SetCookie("admin_session", "", -1, "/", "", false, true)
	c.JSON(200, gin.H{"code": 0})
}
