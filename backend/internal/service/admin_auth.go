package service

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"fmt"
	"time"

	"github.com/redis/go-redis/v9"
	"github.com/rs/zerolog/log"
	"golang.org/x/crypto/bcrypt"
	"gorm.io/gorm"

	"astron-claw/backend/internal/model"
)

const (
	adminSessionTTL    = 86400 * time.Second // 24 hours
	adminSessionPrefix = "admin:session:"
	adminSessionIdx    = "admin:session_idx"
)

// AdminAuth manages admin password auth with MySQL storage and Redis sessions.
type AdminAuth struct {
	db  *gorm.DB
	rdb redis.UniversalClient
}

// NewAdminAuth creates a new AdminAuth.
func NewAdminAuth(db *gorm.DB, rdb redis.UniversalClient) *AdminAuth {
	return &AdminAuth{db: db, rdb: rdb}
}

// IsPasswordSet checks if an admin password has been configured.
func (a *AdminAuth) IsPasswordSet(ctx context.Context) (bool, error) {
	var count int64
	err := a.db.WithContext(ctx).Model(&model.AdminConfig{}).
		Where("`key` = ?", "password_hash").
		Count(&count).Error
	if err != nil {
		return false, err
	}
	return count > 0, nil
}

// SetPassword sets or updates the admin password.
func (a *AdminAuth) SetPassword(ctx context.Context, password string) error {
	if len(password) > 72 {
		return fmt.Errorf("password must not exceed 72 bytes")
	}
	hash, err := bcrypt.GenerateFromPassword([]byte(password), bcrypt.DefaultCost)
	if err != nil {
		return fmt.Errorf("bcrypt hash: %w", err)
	}

	err = a.db.WithContext(ctx).Transaction(func(tx *gorm.DB) error {
		// Set salt to "bcrypt" as a marker for the new scheme
		if err := upsertConfig(tx, "password_salt", "bcrypt"); err != nil {
			return err
		}
		if err := upsertConfig(tx, "password_hash", string(hash)); err != nil {
			return err
		}
		log.Info().Msg("Admin password updated (bcrypt)")
		return nil
	})
	if err != nil {
		return err
	}

	// Invalidate all existing admin sessions
	a.invalidateAllSessions(ctx)

	return nil
}

// VerifyPassword checks if the given password matches the stored hash.
func (a *AdminAuth) VerifyPassword(ctx context.Context, password string) (bool, error) {
	var saltConfig model.AdminConfig
	if err := a.db.WithContext(ctx).Where("`key` = ?", "password_salt").First(&saltConfig).Error; err != nil {
		if err == gorm.ErrRecordNotFound {
			log.Warn().Msg("Admin password verification failed: no password configured")
			return false, nil
		}
		return false, err
	}

	var hashConfig model.AdminConfig
	if err := a.db.WithContext(ctx).Where("`key` = ?", "password_hash").First(&hashConfig).Error; err != nil {
		return false, err
	}

	if saltConfig.Value == "bcrypt" {
		// New bcrypt path
		err := bcrypt.CompareHashAndPassword([]byte(hashConfig.Value), []byte(password))
		if err == bcrypt.ErrMismatchedHashAndPassword {
			return false, nil
		}
		if err != nil {
			return false, fmt.Errorf("bcrypt compare: %w", err)
		}
		return true, nil
	}

	// Legacy SHA-256 path
	salt := saltConfig.Value
	storedHash := hashConfig.Value
	expected := sha256.Sum256([]byte(salt + password))
	expectedHex := hex.EncodeToString(expected[:])

	if subtle.ConstantTimeCompare([]byte(expectedHex), []byte(storedHash)) != 1 {
		return false, nil
	}

	// Auto-upgrade to bcrypt
	log.Info().Msg("Auto-upgrading admin password from SHA-256 to bcrypt")
	if err := a.SetPassword(ctx, password); err != nil {
		log.Error().Err(err).Msg("Failed to auto-upgrade password to bcrypt")
		// Still return true since the password matched
	}

	return true, nil
}

// CreateSession creates a new admin session and stores it in Redis.
func (a *AdminAuth) CreateSession(ctx context.Context) (string, error) {
	b := make([]byte, 32)
	if _, err := rand.Read(b); err != nil {
		return "", fmt.Errorf("generate session token: %w", err)
	}
	token := hex.EncodeToString(b)
	key := adminSessionPrefix + token
	if err := a.rdb.Set(ctx, key, "1", adminSessionTTL).Err(); err != nil {
		return "", fmt.Errorf("store session: %w", err)
	}
	a.rdb.SAdd(ctx, adminSessionIdx, token)
	log.Debug().Msg("Admin session created")
	return token, nil
}

// ValidateSession checks if a session token is valid.
func (a *AdminAuth) ValidateSession(ctx context.Context, sessionToken string) bool {
	if sessionToken == "" {
		return false
	}
	result, err := a.rdb.Exists(ctx, adminSessionPrefix+sessionToken).Result()
	if err != nil || result == 0 {
		log.Debug().Msg("Admin session invalid or expired")
		return false
	}
	log.Debug().Msg("Admin session validated")
	return true
}

// RemoveSession removes an admin session.
func (a *AdminAuth) RemoveSession(ctx context.Context, sessionToken string) {
	if sessionToken != "" {
		a.rdb.Del(ctx, adminSessionPrefix+sessionToken)
		a.rdb.SRem(ctx, adminSessionIdx, sessionToken)
		log.Debug().Msg("Admin session removed")
	}
}

func (a *AdminAuth) invalidateAllSessions(ctx context.Context) {
	tokens, err := a.rdb.SMembers(ctx, adminSessionIdx).Result()
	if err != nil {
		log.Warn().Err(err).Msg("Failed to read admin session index for invalidation")
		return
	}
	if len(tokens) > 0 {
		pipe := a.rdb.Pipeline()
		for _, token := range tokens {
			pipe.Del(ctx, adminSessionPrefix+token)
		}
		pipe.Del(ctx, adminSessionIdx)
		if _, err := pipe.Exec(ctx); err != nil {
			log.Warn().Err(err).Msg("Failed to pipeline-delete admin sessions")
			return
		}
	} else {
		a.rdb.Del(ctx, adminSessionIdx)
	}
	log.Info().Msg("All admin sessions invalidated")
}

func upsertConfig(tx *gorm.DB, key, value string) error {
	var existing model.AdminConfig
	err := tx.Where("`key` = ?", key).First(&existing).Error
	if err == gorm.ErrRecordNotFound {
		return tx.Create(&model.AdminConfig{Key: key, Value: value}).Error
	}
	if err != nil {
		return err
	}
	existing.Value = value
	return tx.Save(&existing).Error
}
