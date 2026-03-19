package infra

import (
	"database/sql"
	"fmt"
	"regexp"
	"time"

	"github.com/rs/zerolog/log"
	"gorm.io/driver/mysql"
	"gorm.io/gorm"
	"gorm.io/gorm/logger"

	"astron-claw/backend/internal/config"
)

var validDBName = regexp.MustCompile(`^[a-zA-Z0-9_]+$`)

func InitDB(cfg config.MysqlConfig, pool config.DBPoolConfig) (*gorm.DB, error) {
	// Ensure database exists
	if err := ensureDatabase(cfg); err != nil {
		return nil, fmt.Errorf("ensure database: %w", err)
	}

	db, err := gorm.Open(mysql.Open(cfg.DSN()), &gorm.Config{
		Logger: logger.Default.LogMode(logger.Silent),
	})
	if err != nil {
		return nil, fmt.Errorf("gorm open: %w", err)
	}

	sqlDB, err := db.DB()
	if err != nil {
		return nil, fmt.Errorf("get sql.DB: %w", err)
	}

	sqlDB.SetMaxIdleConns(pool.MaxIdleConns)
	sqlDB.SetMaxOpenConns(pool.MaxOpenConns)
	sqlDB.SetConnMaxLifetime(time.Duration(pool.ConnMaxLifetime) * time.Second)

	// Verify connectivity
	if err := sqlDB.Ping(); err != nil {
		return nil, fmt.Errorf("mysql ping: %w", err)
	}

	log.Info().
		Str("host", cfg.Host).
		Int("port", cfg.Port).
		Str("database", cfg.Database).
		Msg("MySQL connected")

	return db, nil
}

func ensureDatabase(cfg config.MysqlConfig) error {
	if !validDBName.MatchString(cfg.Database) {
		return fmt.Errorf("invalid database name: %q", cfg.Database)
	}

	db, err := sql.Open("mysql", cfg.DSNWithoutDB())
	if err != nil {
		return err
	}
	defer db.Close()

	query := fmt.Sprintf(
		"CREATE DATABASE IF NOT EXISTS `%s` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci",
		cfg.Database,
	)
	if _, err := db.Exec(query); err != nil {
		return fmt.Errorf("create database: %w", err)
	}

	log.Info().Str("database", cfg.Database).Msg("Ensured database exists")
	return nil
}

func CloseDB(db *gorm.DB) {
	if db == nil {
		return
	}
	sqlDB, err := db.DB()
	if err != nil {
		log.Error().Err(err).Msg("Failed to get sql.DB for closing")
		return
	}
	if err := sqlDB.Close(); err != nil {
		log.Error().Err(err).Msg("Failed to close MySQL")
	} else {
		log.Info().Msg("MySQL connection pool closed")
	}
}
