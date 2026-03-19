package middleware

import (
	"github.com/gin-contrib/cors"
	"github.com/gin-gonic/gin"

	"astron-claw/backend/internal/config"
)

// CORSMiddleware creates a CORS middleware from config.
func CORSMiddleware(cfg config.CorsConfig) gin.HandlerFunc {
	if !cfg.Enabled {
		return func(c *gin.Context) { c.Next() }
	}

	allowAll := len(cfg.Origins) == 1 && cfg.Origins[0] == "*"

	corsConfig := cors.Config{
		AllowOriginFunc: func(origin string) bool {
			for _, o := range cfg.Origins {
				if o == "*" || o == origin {
					return true
				}
			}
			return false
		},
		AllowCredentials: !allowAll,
		AllowMethods:     []string{"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"},
		AllowHeaders:     []string{"*"},
	}

	return cors.New(corsConfig)
}
