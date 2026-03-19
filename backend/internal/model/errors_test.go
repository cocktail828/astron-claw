package model

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/gin-gonic/gin"
)

func TestErrorCodes(t *testing.T) {
	tests := []struct {
		name string
		err  AppError
		code int
	}{
		{"AuthInvalidToken", ErrAuthInvalidToken, http.StatusUnauthorized},
		{"AuthMissingAuth", ErrAuthMissingAuth, http.StatusUnauthorized},
		{"AuthInvalidSession", ErrAuthInvalidSession, http.StatusUnauthorized},
		{"AuthUnauthorized", ErrAuthUnauthorized, http.StatusUnauthorized},
		{"AuthWrongPassword", ErrAuthWrongPassword, http.StatusUnauthorized},
		{"AdminPasswordExists", ErrAdminPasswordExists, http.StatusBadRequest},
		{"AdminPasswordShort", ErrAdminPasswordShort, http.StatusBadRequest},
		{"ChatEmptyMessage", ErrChatEmptyMessage, http.StatusBadRequest},
		{"ChatNoBot", ErrChatNoBot, http.StatusBadRequest},
		{"ChatSendFailed", ErrChatSendFailed, http.StatusInternalServerError},
		{"ChatStreamTimeout", ErrChatStreamTimeout, 0},
		{"ChatInternalError", ErrChatInternalError, 0},
		{"MediaFileTooLarge", ErrMediaFileTooLarge, http.StatusRequestEntityTooLarge},
		{"MediaInvalidFile", ErrMediaInvalidFile, http.StatusBadRequest},
		{"MediaBadURLScheme", ErrMediaBadURLScheme, http.StatusBadRequest},
		{"MediaUnsupportedType", ErrMediaUnsupportedType, http.StatusBadRequest},
		{"MediaTooMany", ErrMediaTooMany, http.StatusBadRequest},
		{"SessionNotFound", ErrSessionNotFound, http.StatusNotFound},
		{"TokenNotFound", ErrTokenNotFound, http.StatusNotFound},
		{"WSInvalidToken", ErrWSInvalidToken, 4001},
		{"WSTokenDeleted", ErrWSTokenDeleted, 4003},
		{"WSServerRestart", ErrWSServerRestart, 4000},
		{"WSEvicted", ErrWSEvicted, 4005},
		{"BotUnknownError", ErrBotUnknownError, 0},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if tt.err.Code != tt.code {
				t.Errorf("%s: code = %d, want %d", tt.name, tt.err.Code, tt.code)
			}
			if tt.err.Message == "" {
				t.Errorf("%s: message should not be empty", tt.name)
			}
		})
	}
}

func TestErrorResponse(t *testing.T) {
	gin.SetMode(gin.TestMode)

	t.Run("standard error", func(t *testing.T) {
		w := httptest.NewRecorder()
		c, _ := gin.CreateTestContext(w)
		ErrorResponse(c, ErrChatNoBot)

		if w.Code != http.StatusBadRequest {
			t.Errorf("status = %d, want %d", w.Code, http.StatusBadRequest)
		}
		var resp map[string]interface{}
		json.Unmarshal(w.Body.Bytes(), &resp)
		if resp["error"] != "No bot connected" {
			t.Errorf("error = %v, want 'No bot connected'", resp["error"])
		}
	})

	t.Run("with detail", func(t *testing.T) {
		w := httptest.NewRecorder()
		c, _ := gin.CreateTestContext(w)
		ErrorResponse(c, ErrSessionNotFound, "sess-abc")

		var resp map[string]interface{}
		json.Unmarshal(w.Body.Bytes(), &resp)
		errMsg, _ := resp["error"].(string)
		if errMsg != "Session not found: sess-abc" {
			t.Errorf("error = %q, want 'Session not found: sess-abc'", errMsg)
		}
	})

	t.Run("zero code defaults to 500", func(t *testing.T) {
		w := httptest.NewRecorder()
		c, _ := gin.CreateTestContext(w)
		ErrorResponse(c, ErrChatStreamTimeout)

		if w.Code != http.StatusInternalServerError {
			t.Errorf("status = %d, want 500", w.Code)
		}
	})
}
