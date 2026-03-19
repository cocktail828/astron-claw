package router

import (
	"github.com/gin-gonic/gin"
	"github.com/redis/go-redis/v9"
	"gorm.io/gorm"

	"github.com/hygao1024/astron-claw/backend/internal/config"
	"github.com/hygao1024/astron-claw/backend/internal/infra/storage"
	"github.com/hygao1024/astron-claw/backend/internal/middleware"
	"github.com/hygao1024/astron-claw/backend/internal/service"
)

// App holds all application dependencies.
type App struct {
	DB        *gorm.DB
	RDB       redis.UniversalClient
	TokenMgr  *service.TokenManager
	AdminAuth *service.AdminAuth
	MediaMgr  *service.MediaManager
	Bridge    *service.ConnectionBridge
	Queue     service.MessageQueue
	Storage   storage.ObjectStorage
	Config    *config.AppConfig
}

// SetupRouter configures all routes and middleware.
func SetupRouter(app *App) *gin.Engine {
	gin.SetMode(gin.ReleaseMode)
	r := gin.New()
	r.Use(gin.Recovery())

	// CORS middleware
	r.Use(middleware.CORSMiddleware(app.Config.CORS))

	// Token auth middleware
	r.Use(middleware.TokenAuth(app.TokenMgr, app.RDB))

	// Health
	r.GET("/api/health", app.healthCheck)

	// Metrics
	r.GET("/api/metrics", app.getMetrics)
	r.DELETE("/api/metrics", app.deleteMetrics)

	// Tokens (public)
	r.POST("/api/token", app.createToken)
	r.POST("/api/token/validate", app.validateToken)

	// Admin auth
	r.GET("/api/admin/auth/status", app.adminAuthStatus)
	r.POST("/api/admin/auth/setup", app.adminAuthSetup)
	r.POST("/api/admin/auth/login", app.adminAuthLogin)
	r.POST("/api/admin/auth/logout", app.adminAuthLogout)

	// Admin
	r.GET("/api/admin/tokens", app.listTokens)
	r.POST("/api/admin/tokens", app.adminCreateToken)
	r.PATCH("/api/admin/tokens/:token", app.adminUpdateToken)
	r.DELETE("/api/admin/tokens/:token", app.adminDeleteToken)
	r.POST("/api/admin/cleanup", app.adminCleanup)

	// Media
	r.POST("/api/media/upload", app.uploadMedia)

	// SSE (bridge)
	r.POST("/bridge/chat", app.chatSSE)
	r.GET("/bridge/chat/sessions", app.listSessions)
	r.POST("/bridge/chat/sessions", app.createSession)

	// WebSocket
	r.GET("/bridge/bot", app.wsBot)

	return r
}
