package client

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"sync"

	"github.com/gorilla/websocket"
	"github.com/hygao1024/astron-claw/probe/model"
)

const probeReply = "astron-claw-probe-ok"

// Bot simulates a bot plugin that connects via WebSocket and replies with a fixed string.
type Bot struct {
	wsURL string
	token string
	conn  *websocket.Conn
	ready chan struct{}
	done  chan struct{}
	mu    sync.Mutex
	err   error
}

func NewBot(wsURL, token string) *Bot {
	return &Bot{
		wsURL: wsURL,
		token: token,
		ready: make(chan struct{}),
		done:  make(chan struct{}),
	}
}

// Connect dials the WebSocket endpoint and starts the read loop.
func (b *Bot) Connect(ctx context.Context) error {
	url := b.wsURL + "/bridge/bot?token=" + b.token
	conn, resp, err := websocket.DefaultDialer.DialContext(ctx, url, nil)
	if err != nil {
		if resp != nil {
			return fmt.Errorf("ws dial: http %d", resp.StatusCode)
		}
		return fmt.Errorf("ws dial: %w", err)
	}
	b.conn = conn
	go b.readLoop()
	return nil
}

// WaitReady blocks until the read loop is running or context is cancelled.
func (b *Bot) WaitReady(ctx context.Context) error {
	select {
	case <-b.ready:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

// Close gracefully closes the WebSocket connection.
func (b *Bot) Close() error {
	if b.conn == nil {
		return nil
	}
	err := b.conn.WriteMessage(
		websocket.CloseMessage,
		websocket.FormatCloseMessage(websocket.CloseNormalClosure, ""),
	)
	if err != nil {
		b.conn.Close()
		return err
	}
	b.conn.Close()
	<-b.done
	return nil
}

func (b *Bot) readLoop() {
	defer close(b.done)
	close(b.ready) // signal ready as soon as read loop starts

	for {
		_, msg, err := b.conn.ReadMessage()
		if err != nil {
			if websocket.IsCloseError(err, websocket.CloseNormalClosure, 4003) {
				return
			}
			slog.Debug("bot read error", "err", err)
			return
		}

		var req model.JSONRPCRequest
		if err := json.Unmarshal(msg, &req); err != nil {
			slog.Debug("bot unmarshal error", "err", err)
			continue
		}

		if req.Method == "session/prompt" {
			b.handlePrompt(req)
		}
	}
}

func (b *Bot) handlePrompt(req model.JSONRPCRequest) {
	sessionID := ""
	if req.Params != nil {
		sessionID = req.Params.SessionID
	}
	slog.Debug("bot received prompt", "id", req.ID, "sessionId", sessionID)

	// 1. agent_message_chunk
	b.send(model.SessionUpdateMsg{
		Method: "session/update",
		Params: model.SessionUpdateParams{
			SessionID: sessionID,
			Update: model.UpdatePayload{
				SessionUpdate: "agent_message_chunk",
				Content:       &model.TextContent{Type: "text", Text: probeReply},
			},
		},
	})

	// 2. agent_message_final -> produces "done" SSE event on chat side
	b.send(model.SessionUpdateMsg{
		Method: "session/update",
		Params: model.SessionUpdateParams{
			SessionID: sessionID,
			Update: model.UpdatePayload{
				SessionUpdate: "agent_message_final",
				Content:       &model.TextContent{Type: "text", Text: probeReply},
			},
		},
	})

	// 3. JSON-RPC Response (completion signal)
	b.send(model.JSONRPCResponse{
		JSONRPC:   "2.0",
		ID:        req.ID,
		SessionID: sessionID,
		Result:    &model.StopResult{StopReason: "end_turn"},
	})
}

func (b *Bot) send(v any) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if err := b.conn.WriteJSON(v); err != nil {
		slog.Debug("bot send error", "err", err)
	}
}
