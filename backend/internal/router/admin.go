package router

import (
	"sort"
	"strconv"

	"github.com/gin-gonic/gin"
	"github.com/rs/zerolog/log"

	"github.com/hygao1024/astron-claw/backend/internal/model"
)

func (app *App) requireAdmin(c *gin.Context) bool {
	adminSession, _ := c.Cookie("admin_session")
	if !app.AdminAuth.ValidateSession(c.Request.Context(), adminSession) {
		log.Warn().Msg("Admin auth rejected: missing or invalid session cookie")
		model.ErrorResponse(c, model.ErrAuthUnauthorized)
		return false
	}
	return true
}

func (app *App) listTokens(c *gin.Context) {
	if !app.requireAdmin(c) {
		return
	}
	ctx := c.Request.Context()

	page, _ := strconv.Atoi(c.DefaultQuery("page", "1"))
	pageSize, _ := strconv.Atoi(c.DefaultQuery("page_size", "20"))
	search := c.DefaultQuery("search", "")
	sortBy := c.DefaultQuery("sort_by", "created_at")
	sortOrder := c.DefaultQuery("sort_order", "desc")
	botStatus := c.DefaultQuery("bot_status", "")

	if page < 1 {
		page = 1
	}
	if pageSize < 1 {
		pageSize = 20
	}
	if pageSize > 100 {
		pageSize = 100
	}

	// Fetch all tokens (for bot status merge)
	data, err := app.TokenMgr.ListAll(ctx, 1, 10000, search)
	if err != nil {
		c.JSON(500, gin.H{"code": 500, "error": err.Error()})
		return
	}

	connections := app.Bridge.GetConnectionsSummary(ctx)

	type tokenInfo struct {
		Token     string  `json:"token"`
		Name      string  `json:"name"`
		CreatedAt float64 `json:"created_at"`
		ExpiresAt float64 `json:"expires_at"`
		BotOnline bool    `json:"bot_online"`
	}

	allTokens := make([]tokenInfo, len(data.Items))
	for i, t := range data.Items {
		allTokens[i] = tokenInfo{
			Token:     t.Token,
			Name:      t.Name,
			CreatedAt: t.CreatedAt,
			ExpiresAt: t.ExpiresAt,
			BotOnline: connections[t.Token],
		}
	}

	// Global stats
	globalOnline := 0
	for _, t := range allTokens {
		if t.BotOnline {
			globalOnline++
		}
	}

	// Filter by bot status
	filtered := allTokens
	if botStatus == "online" {
		var f []tokenInfo
		for _, t := range filtered {
			if t.BotOnline {
				f = append(f, t)
			}
		}
		filtered = f
	}

	// Sort
	reverse := sortOrder == "desc"
	if sortBy == "bot_online" {
		sort.Slice(filtered, func(i, j int) bool {
			if filtered[i].BotOnline != filtered[j].BotOnline {
				if reverse {
					return filtered[i].BotOnline
				}
				return filtered[j].BotOnline
			}
			if reverse {
				return filtered[i].CreatedAt > filtered[j].CreatedAt
			}
			return filtered[i].CreatedAt < filtered[j].CreatedAt
		})
	} else {
		sort.Slice(filtered, func(i, j int) bool {
			if reverse {
				return filtered[i].CreatedAt > filtered[j].CreatedAt
			}
			return filtered[i].CreatedAt < filtered[j].CreatedAt
		})
	}

	// Paginate
	total := len(filtered)
	offset := (page - 1) * pageSize
	end := offset + pageSize
	if offset > total {
		offset = total
	}
	if end > total {
		end = total
	}
	pageItems := filtered[offset:end]

	c.JSON(200, gin.H{
		"code":         0,
		"tokens":       pageItems,
		"total":        total,
		"page":         page,
		"page_size":    pageSize,
		"online_bots":  globalOnline,
		"total_tokens": len(allTokens),
	})
}

func (app *App) adminCreateToken(c *gin.Context) {
	if !app.requireAdmin(c) {
		return
	}

	var body struct {
		Name      string `json:"name"`
		ExpiresIn int    `json:"expires_in"`
	}
	body.ExpiresIn = 86400 // default
	if err := c.ShouldBindJSON(&body); err != nil {
		// Allow empty body
	}

	token, err := app.TokenMgr.Generate(c.Request.Context(), body.Name, body.ExpiresIn)
	if err != nil {
		c.JSON(500, gin.H{"code": 500, "error": err.Error()})
		return
	}
	log.Info().Str("token", token[:16]+"...").Str("name", body.Name).Msg("Admin created token")
	c.JSON(200, gin.H{"code": 0, "token": token})
}

func (app *App) adminDeleteToken(c *gin.Context) {
	if !app.requireAdmin(c) {
		return
	}
	tokenValue := c.Param("token")

	if err := app.TokenMgr.Remove(c.Request.Context(), tokenValue); err != nil {
		c.JSON(500, gin.H{"code": 500, "error": err.Error()})
		return
	}
	app.Bridge.RemoveBotSessions(c.Request.Context(), tokenValue)
	log.Info().Str("token", tokenValue[:16]+"...").Msg("Admin deleted token")
	c.JSON(200, gin.H{"code": 0})
}

func (app *App) adminUpdateToken(c *gin.Context) {
	if !app.requireAdmin(c) {
		return
	}
	tokenValue := c.Param("token")

	var body map[string]interface{}
	if err := c.ShouldBindJSON(&body); err != nil {
		c.JSON(400, gin.H{"code": 400, "error": "Invalid request"})
		return
	}

	var name *string
	var expiresIn *int
	if v, ok := body["name"].(string); ok {
		name = &v
	}
	if v, ok := body["expires_in"].(float64); ok {
		ei := int(v)
		expiresIn = &ei
	}

	found, err := app.TokenMgr.Update(c.Request.Context(), tokenValue, name, expiresIn)
	if err != nil {
		c.JSON(500, gin.H{"code": 500, "error": err.Error()})
		return
	}
	if !found {
		model.ErrorResponse(c, model.ErrTokenNotFound)
		return
	}
	log.Info().Str("token", tokenValue[:16]+"...").Msg("Admin updated token")
	c.JSON(200, gin.H{"code": 0})
}

func (app *App) adminCleanup(c *gin.Context) {
	if !app.requireAdmin(c) {
		return
	}
	ctx := c.Request.Context()

	tokenCount, err := app.TokenMgr.CleanupExpired(ctx)
	if err != nil {
		c.JSON(500, gin.H{"code": 500, "error": err.Error()})
		return
	}
	sessionCount, err := app.Bridge.CleanupOldSessions(ctx, 30)
	if err != nil {
		c.JSON(500, gin.H{"code": 500, "error": err.Error()})
		return
	}
	log.Info().Int("tokens", tokenCount).Int("sessions", sessionCount).Msg("Admin cleanup")
	c.JSON(200, gin.H{"code": 0, "removed_tokens": tokenCount, "removed_sessions": sessionCount})
}
