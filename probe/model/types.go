package model

import "fmt"

// ProbeResult is the final output of the probe run.
type ProbeResult struct {
	Code int    `json:"code"`
	Info string `json:"info"`
	Time int64  `json:"time"` // ms
}

// --- HTTP responses ---

type TokenResponse struct {
	Code  int    `json:"code"`
	Token string `json:"token,omitempty"`
	Error string `json:"error,omitempty"`
}

type CodeResponse struct {
	Code  int    `json:"code"`
	Error string `json:"error,omitempty"`
}

// --- SSE ---

type SessionEvent struct {
	SessionID     string `json:"sessionId"`
	SessionNumber int    `json:"sessionNumber"`
}

// ContentEvent is used for chunk, done, and error SSE events (all use {"content": "..."}).
type ContentEvent struct {
	Content string `json:"content"`
}

// --- JSON-RPC (Bot WebSocket) ---

type JSONRPCRequest struct {
	JSONRPC string        `json:"jsonrpc"`
	ID      string        `json:"id"`
	Method  string        `json:"method"`
	Params  *PromptParams `json:"params,omitempty"`
}

type PromptParams struct {
	SessionID string        `json:"sessionId"`
	Prompt    PromptContent `json:"prompt"`
}

type PromptContent struct {
	Content []ContentItem `json:"content"`
}

type ContentItem struct {
	Type    string `json:"type"`
	Content string `json:"content"`
}

// SessionUpdateMsg is a JSON-RPC Notification sent by the bot (no id, no jsonrpc).
type SessionUpdateMsg struct {
	Method string              `json:"method"`
	Params SessionUpdateParams `json:"params"`
}

type SessionUpdateParams struct {
	SessionID string        `json:"sessionId"`
	Update    UpdatePayload `json:"update"`
}

type UpdatePayload struct {
	SessionUpdate string       `json:"sessionUpdate"`
	Content       *TextContent `json:"content,omitempty"`
}

type TextContent struct {
	Type string `json:"type"`
	Text string `json:"text"`
}

// JSONRPCResponse is the completion signal sent by the bot.
type JSONRPCResponse struct {
	JSONRPC   string      `json:"jsonrpc"`
	ID        string      `json:"id"`
	SessionID string      `json:"sessionId"`
	Result    *StopResult `json:"result,omitempty"`
}

type StopResult struct {
	StopReason string `json:"stopReason"`
}

// --- Error with code ---

// CodeError carries an integer error code alongside the message.
type CodeError struct {
	ErrCode int
	Msg     string
}

func (e *CodeError) Error() string { return e.Msg }

func NewCodeError(code int, format string, args ...any) *CodeError {
	return &CodeError{ErrCode: code, Msg: fmt.Sprintf(format, args...)}
}
