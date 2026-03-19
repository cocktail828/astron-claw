package middleware

import (
	"github.com/gin-contrib/cors"
	"github.com/gin-gonic/gin"

	"github.com/hygao1024/astron-claw/backend/internal/config"
)

// CORSMiddleware creates a CORS middleware from config.
func CORSMiddleware(cfg config.CorsConfig) gin.HandlerFunc {
	if !cfg.Enabled {
		return func(c *gin.Context) { c.Next() }
	}

	corsConfig := cors.Config{
		AllowOriginFunc: func(origin string) bool {
			for _, o := range cfg.Origins {
				if o == "*" || o == origin {
					return true
				}
			}
			return false
		},
		AllowCredentials: true,
		AllowMethods:     []string{"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"},
		AllowHeaders:     []string{"*"},
	}

	return cors.New(corsConfig)
}
