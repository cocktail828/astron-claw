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
	"gorm.io/gorm"

	"github.com/hygao1024/astron-claw/backend/internal/model"
)

const (
	adminSessionTTL    = 86400 * time.Second // 24 hours
	adminSessionPrefix = "admin:session:"
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
	saltBytes := make([]byte, 16)
	if _, err := rand.Read(saltBytes); err != nil {
		return fmt.Errorf("generate salt: %w", err)
	}
	salt := hex.EncodeToString(saltBytes)

	hash := sha256.Sum256([]byte(salt + password))
	pwHash := hex.EncodeToString(hash[:])

	return a.db.WithContext(ctx).Transaction(func(tx *gorm.DB) error {
		// Upsert salt
		if err := upsertConfig(tx, "password_salt", salt); err != nil {
			return err
		}
		// Upsert hash
		if err := upsertConfig(tx, "password_hash", pwHash); err != nil {
			return err
		}
		log.Info().Msg("Admin password updated")
		return nil
	})
}

// VerifyPassword checks if the given password matches the stored hash.
func (a *AdminAuth) VerifyPassword(ctx context.Context, password string) (bool, error) {
	var salt, storedHash string

	var saltConfig model.AdminConfig
	if err := a.db.WithContext(ctx).Where("`key` = ?", "password_salt").First(&saltConfig).Error; err != nil {
		if err == gorm.ErrRecordNotFound {
			log.Warn().Msg("Admin password verification failed: no password configured")
			return false, nil
		}
		return false, err
	}
	salt = saltConfig.Value

	var hashConfig model.AdminConfig
	if err := a.db.WithContext(ctx).Where("`key` = ?", "password_hash").First(&hashConfig).Error; err != nil {
		return false, err
	}
	storedHash = hashConfig.Value

	expected := sha256.Sum256([]byte(salt + password))
	expectedHex := hex.EncodeToString(expected[:])

	// Constant-time comparison
	return subtle.ConstantTimeCompare([]byte(expectedHex), []byte(storedHash)) == 1, nil
}

// CreateSession creates a new admin session and stores it in Redis.
func (a *AdminAuth) CreateSession(ctx context.Context) (string, error) {
	b := make([]byte, 32)
	if _, err := rand.Read(b); err != nil {
		return "", fmt.Errorf("generate session token: %w", err)
	}
	token := hex.EncodeToString(b)
	if err := a.rdb.Set(ctx, adminSessionPrefix+token, "1", adminSessionTTL).Err(); err != nil {
		return "", fmt.Errorf("store session: %w", err)
	}
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
		log.Debug().Msg("Admin session removed")
	}
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
