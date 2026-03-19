package pkg

import (
	"encoding/json"
	"fmt"
)

// FormatSSEEvent formats an SSE event with the given type and data.
func FormatSSEEvent(eventType string, data interface{}) string {
	var dataStr string
	switch v := data.(type) {
	case string:
		dataStr = v
	case []byte:
		dataStr = string(v)
	default:
		b, _ := json.Marshal(v)
		dataStr = string(b)
	}
	return fmt.Sprintf("event: %s\ndata: %s\n\n", eventType, dataStr)
}

// FormatSSEComment formats an SSE comment (heartbeat).
func FormatSSEComment() string {
	return ": heartbeat\n\n"
}
