package infra

import (
	"io"
	"os"
	"path/filepath"
	"strings"

	"github.com/rs/zerolog"
	"github.com/rs/zerolog/log"
	"gopkg.in/natefinch/lumberjack.v2"
)

func SetupLogger(level string) {
	logDir := os.Getenv("LOG_DIR")
	if logDir == "" {
		logDir = "logs"
	}
	if err := os.MkdirAll(logDir, 0o755); err != nil {
		log.Fatal().Err(err).Msg("Failed to create log directory")
	}

	useJSON := strings.ToLower(os.Getenv("LOG_FORMAT_JSON")) == "true"

	lvl, err := zerolog.ParseLevel(strings.ToLower(level))
	if err != nil {
		lvl = zerolog.InfoLevel
	}
	zerolog.SetGlobalLevel(lvl)

	// File writer: all levels (server.log)
	serverLog := &lumberjack.Logger{
		Filename: filepath.Join(logDir, "server.log"),
		MaxSize:  50, // MB
		MaxAge:   30, // days
		Compress: true,
	}

	// Error-only file writer (error.log) - WARNING and above
	errorLog := &lumberjack.Logger{
		Filename: filepath.Join(logDir, "error.log"),
		MaxSize:  50,
		MaxAge:   30,
		Compress: true,
	}
	errorWriter := zerolog.MultiLevelWriter(
		&levelFilterWriter{writer: errorLog, minLevel: zerolog.WarnLevel},
	)

	var writers []io.Writer

	// Console output
	if useJSON {
		writers = append(writers, os.Stderr)
	} else {
		writers = append(writers, zerolog.ConsoleWriter{
			Out:        os.Stderr,
			TimeFormat: "2006-01-02 15:04:05.000",
		})
	}

	// File outputs
	writers = append(writers, serverLog, errorWriter)

	multi := zerolog.MultiLevelWriter(writers...)
	log.Logger = zerolog.New(multi).With().Timestamp().Caller().Logger()

	log.Info().Str("level", level).Bool("json", useJSON).Str("dir", logDir).Msg("Logging initialised")
}

// levelFilterWriter only writes messages at or above the specified level.
type levelFilterWriter struct {
	writer   io.Writer
	minLevel zerolog.Level
}

func (w *levelFilterWriter) Write(p []byte) (n int, err error) {
	return len(p), nil
}

func (w *levelFilterWriter) WriteLevel(level zerolog.Level, p []byte) (n int, err error) {
	if level >= w.minLevel {
		return w.writer.Write(p)
	}
	return len(p), nil
}
