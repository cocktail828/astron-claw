package config

import (
	"os"
	"strings"
	"testing"
)

func TestLoad_Defaults(t *testing.T) {
	// Clear env to test defaults
	envKeys := []string{
		"MYSQL_HOST", "MYSQL_PORT", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_DATABASE",
		"REDIS_HOST", "REDIS_PORT", "REDIS_PASSWORD", "REDIS_DB", "REDIS_ADDRS",
		"SERVER_HOST", "SERVER_PORT", "SERVER_LOG_LEVEL",
		"QUEUE_TYPE", "QUEUE_MAX_STREAM_LEN",
		"OSS_TYPE", "OSS_ENDPOINT", "OSS_PUBLIC_ENDPOINT", "OSS_ACCESS_KEY", "OSS_SECRET_KEY", "OSS_BUCKET",
		"OTLP_ENABLED", "CORS_ENABLED", "CORS_ORIGINS",
	}
	saved := make(map[string]string)
	for _, k := range envKeys {
		saved[k] = os.Getenv(k)
		os.Unsetenv(k)
	}
	defer func() {
		for k, v := range saved {
			if v != "" {
				os.Setenv(k, v)
			}
		}
	}()

	cfg := Load()

	if cfg.MySQL.Host != "127.0.0.1" {
		t.Errorf("MySQL.Host = %q, want 127.0.0.1", cfg.MySQL.Host)
	}
	if cfg.MySQL.Port != 3306 {
		t.Errorf("MySQL.Port = %d, want 3306", cfg.MySQL.Port)
	}
	if cfg.MySQL.Database != "astron_claw" {
		t.Errorf("MySQL.Database = %q, want astron_claw", cfg.MySQL.Database)
	}
	if cfg.Redis.Host != "127.0.0.1" {
		t.Errorf("Redis.Host = %q, want 127.0.0.1", cfg.Redis.Host)
	}
	if cfg.Redis.IsCluster() {
		t.Error("Redis should default to standalone (not cluster)")
	}
	if cfg.Server.Port != 8765 {
		t.Errorf("Server.Port = %d, want 8765", cfg.Server.Port)
	}
	if cfg.Queue.Type != "redis_stream" {
		t.Errorf("Queue.Type = %q, want redis_stream", cfg.Queue.Type)
	}
	if cfg.Storage.Type != "s3" {
		t.Errorf("Storage.Type = %q, want s3", cfg.Storage.Type)
	}
	if cfg.OTLP.Enabled {
		t.Error("OTLP.Enabled should default to false")
	}
	if !cfg.CORS.Enabled {
		t.Error("CORS.Enabled should default to true")
	}
	if len(cfg.CORS.Origins) != 1 || cfg.CORS.Origins[0] != "*" {
		t.Errorf("CORS.Origins = %v, want [*]", cfg.CORS.Origins)
	}
}

func TestLoad_EnvOverrides(t *testing.T) {
	os.Setenv("MYSQL_HOST", "db.example.com")
	os.Setenv("MYSQL_PORT", "3307")
	os.Setenv("REDIS_ADDRS", "10.0.0.1:6379,10.0.0.2:6379")
	os.Setenv("SERVER_PORT", "9999")
	os.Setenv("CORS_ORIGINS", "http://a.com, http://b.com")
	defer func() {
		os.Unsetenv("MYSQL_HOST")
		os.Unsetenv("MYSQL_PORT")
		os.Unsetenv("REDIS_ADDRS")
		os.Unsetenv("SERVER_PORT")
		os.Unsetenv("CORS_ORIGINS")
	}()

	cfg := Load()

	if cfg.MySQL.Host != "db.example.com" {
		t.Errorf("MySQL.Host = %q, want db.example.com", cfg.MySQL.Host)
	}
	if cfg.MySQL.Port != 3307 {
		t.Errorf("MySQL.Port = %d, want 3307", cfg.MySQL.Port)
	}
	if !cfg.Redis.IsCluster() {
		t.Error("Redis should be cluster mode when multiple REDIS_ADDRS provided")
	}
	if len(cfg.Redis.Addrs) != 2 || cfg.Redis.Addrs[0] != "10.0.0.1:6379" || cfg.Redis.Addrs[1] != "10.0.0.2:6379" {
		t.Errorf("Redis.Addrs = %v, want [10.0.0.1:6379 10.0.0.2:6379]", cfg.Redis.Addrs)
	}
	if cfg.Server.Port != 9999 {
		t.Errorf("Server.Port = %d, want 9999", cfg.Server.Port)
	}
	if len(cfg.CORS.Origins) != 2 || cfg.CORS.Origins[0] != "http://a.com" || cfg.CORS.Origins[1] != "http://b.com" {
		t.Errorf("CORS.Origins = %v, want [http://a.com http://b.com]", cfg.CORS.Origins)
	}
}

func TestMysqlConfig_DSN(t *testing.T) {
	cfg := MysqlConfig{Host: "localhost", Port: 3306, User: "root", Password: "pass", Database: "test"}
	dsn := cfg.DSN()
	// FormatDSN includes all params; check key substrings
	for _, want := range []string{"root:pass@tcp(localhost:3306)/test?", "charset=utf8mb4", "parseTime=True", "loc=UTC"} {
		if !strings.Contains(dsn, want) {
			t.Errorf("DSN = %q, missing %q", dsn, want)
		}
	}
}

func TestMysqlConfig_DSNWithoutDB(t *testing.T) {
	cfg := MysqlConfig{Host: "localhost", Port: 3306, User: "root", Password: "pass", Database: "test"}
	dsn := cfg.DSNWithoutDB()
	for _, want := range []string{"root:pass@tcp(localhost:3306)/?", "charset=utf8mb4", "parseTime=True", "loc=UTC"} {
		if !strings.Contains(dsn, want) {
			t.Errorf("DSNWithoutDB = %q, missing %q", dsn, want)
		}
	}
	if strings.Contains(dsn, "/test?") {
		t.Errorf("DSNWithoutDB should not contain database name, got %q", dsn)
	}
}

func TestGetEnv(t *testing.T) {
	os.Setenv("TEST_KEY_XYZ", "hello")
	defer os.Unsetenv("TEST_KEY_XYZ")

	if v := getEnv("TEST_KEY_XYZ", "default"); v != "hello" {
		t.Errorf("got %q, want hello", v)
	}
	if v := getEnv("NONEXISTENT_KEY_XYZ", "default"); v != "default" {
		t.Errorf("got %q, want default", v)
	}
}

func TestGetEnvInt(t *testing.T) {
	os.Setenv("TEST_INT_XYZ", "42")
	defer os.Unsetenv("TEST_INT_XYZ")

	if v := getEnvInt("TEST_INT_XYZ", 0); v != 42 {
		t.Errorf("got %d, want 42", v)
	}
	if v := getEnvInt("NONEXISTENT_INT_XYZ", 99); v != 99 {
		t.Errorf("got %d, want 99", v)
	}

	os.Setenv("TEST_INT_XYZ", "notanumber")
	if v := getEnvInt("TEST_INT_XYZ", 7); v != 7 {
		t.Errorf("got %d, want 7 for invalid int", v)
	}
}

func TestGetEnvBool(t *testing.T) {
	os.Setenv("TEST_BOOL_XYZ", "true")
	defer os.Unsetenv("TEST_BOOL_XYZ")

	if v := getEnvBool("TEST_BOOL_XYZ", false); !v {
		t.Error("expected true")
	}

	os.Setenv("TEST_BOOL_XYZ", "false")
	if v := getEnvBool("TEST_BOOL_XYZ", true); v {
		t.Error("expected false")
	}

	if v := getEnvBool("NONEXISTENT_BOOL_XYZ", true); !v {
		t.Error("expected default true")
	}
}

func TestSplitCSV(t *testing.T) {
	tests := []struct {
		input    string
		expected []string
	}{
		{"a,b,c", []string{"a", "b", "c"}},
		{" a , b , c ", []string{"a", "b", "c"}},
		{"*", []string{"*"}},
		{"", []string{}},
		{"a,,b", []string{"a", "b"}},
	}
	for _, tt := range tests {
		result := splitCSV(tt.input)
		if len(result) != len(tt.expected) {
			t.Errorf("splitCSV(%q) = %v, want %v", tt.input, result, tt.expected)
			continue
		}
		for i := range result {
			if result[i] != tt.expected[i] {
				t.Errorf("splitCSV(%q)[%d] = %q, want %q", tt.input, i, result[i], tt.expected[i])
			}
		}
	}
}
