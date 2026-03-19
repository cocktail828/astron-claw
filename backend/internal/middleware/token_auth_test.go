package middleware

import (
	"testing"
)

func TestIsProtected(t *testing.T) {
	tests := []struct {
		path     string
		expected bool
	}{
		{"/bridge/chat", true},
		{"/bridge/chat/sessions", true},
		{"/api/media/upload", true},
		{"/bridge/bot", false}, // excluded: WS handles its own auth
		{"/api/health", false},
		{"/api/token", false},
		{"/api/admin/auth/status", false},
		{"/api/admin/tokens", false},
		{"/api/metrics", false},
	}
	for _, tt := range tests {
		result := isProtected(tt.path)
		if result != tt.expected {
			t.Errorf("isProtected(%q) = %v, want %v", tt.path, result, tt.expected)
		}
	}
}

func TestExtractBearer(t *testing.T) {
	tests := []struct {
		raw      string
		expected string
	}{
		{"", ""},
		{"Bearer sk-abc123", "sk-abc123"},
		{"bearer sk-abc123", "sk-abc123"},
		{"BEARER sk-abc123", "sk-abc123"},
		{"Bearer  sk-abc123 ", "sk-abc123"},
		{"Bearer ", ""},
		{"sk-abc123", "sk-abc123"}, // X-Api-Key style
		{"some-raw-token", "some-raw-token"},
	}
	for _, tt := range tests {
		result := extractBearer(tt.raw)
		if result != tt.expected {
			t.Errorf("extractBearer(%q) = %q, want %q", tt.raw, result, tt.expected)
		}
	}
}
