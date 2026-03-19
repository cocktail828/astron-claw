package pkg

import (
	"encoding/json"
	"testing"
)

func TestNewJSONRPCRequest(t *testing.T) {
	req := NewJSONRPCRequest("test-id", "session/prompt", map[string]string{"key": "val"})
	if req.JSONRPC != "2.0" {
		t.Errorf("expected jsonrpc=2.0, got %s", req.JSONRPC)
	}
	if req.ID != "test-id" {
		t.Errorf("expected id=test-id, got %s", req.ID)
	}
	if req.Method != "session/prompt" {
		t.Errorf("expected method=session/prompt, got %s", req.Method)
	}

	// Verify JSON marshaling
	data, err := json.Marshal(req)
	if err != nil {
		t.Fatal(err)
	}
	var m map[string]interface{}
	if err := json.Unmarshal(data, &m); err != nil {
		t.Fatal(err)
	}
	if m["jsonrpc"] != "2.0" {
		t.Errorf("JSON: expected jsonrpc=2.0, got %v", m["jsonrpc"])
	}
	if m["id"] != "test-id" {
		t.Errorf("JSON: expected id=test-id, got %v", m["id"])
	}
}
