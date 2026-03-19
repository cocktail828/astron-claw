package pkg

import (
	"strings"
	"testing"
)

func TestFormatSSEEvent_MapData(t *testing.T) {
	result := FormatSSEEvent("chunk", map[string]interface{}{"content": "hello"})
	if !strings.HasPrefix(result, "event: chunk\n") {
		t.Errorf("expected event prefix, got %q", result)
	}
	if !strings.Contains(result, `"content":"hello"`) {
		t.Errorf("expected JSON data, got %q", result)
	}
	if !strings.HasSuffix(result, "\n\n") {
		t.Errorf("expected double newline suffix, got %q", result)
	}
}

func TestFormatSSEEvent_StringData(t *testing.T) {
	result := FormatSSEEvent("message", "plain text")
	expected := "event: message\ndata: plain text\n\n"
	if result != expected {
		t.Errorf("got %q, want %q", result, expected)
	}
}

func TestFormatSSEEvent_ByteData(t *testing.T) {
	result := FormatSSEEvent("data", []byte("bytes"))
	expected := "event: data\ndata: bytes\n\n"
	if result != expected {
		t.Errorf("got %q, want %q", result, expected)
	}
}

func TestFormatSSEComment(t *testing.T) {
	result := FormatSSEComment()
	expected := ": heartbeat\n\n"
	if result != expected {
		t.Errorf("got %q, want %q", result, expected)
	}
}

func TestFormatSSEEvent_SessionEvent(t *testing.T) {
	result := FormatSSEEvent("session", map[string]interface{}{
		"sessionId":     "abc-123",
		"sessionNumber": 1,
	})
	if !strings.HasPrefix(result, "event: session\n") {
		t.Errorf("expected session event prefix, got %q", result)
	}
	if !strings.Contains(result, `"sessionId":"abc-123"`) {
		t.Errorf("expected sessionId in data, got %q", result)
	}
}
