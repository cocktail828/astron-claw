package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"os"
	"strings"
	"time"

	"github.com/hygao1024/astron-claw/probe/client"
	"github.com/hygao1024/astron-claw/probe/model"
)

func main() {
	if len(os.Args) < 3 {
		fmt.Fprintf(os.Stderr, "Usage: probe <domain> <admin_password>\n")
		os.Exit(1)
	}

	domain := os.Args[1]
	adminPassword := os.Args[2]

	result := run(domain, adminPassword)

	out, _ := json.Marshal(result)
	fmt.Println(string(out))

	if result.Code != 0 {
		os.Exit(1)
	}
}

func run(domain, adminPassword string) model.ProbeResult {
	start := time.Now()
	baseURL := "http://" + domain
	wsURL := "ws://" + domain
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()

	// Step 0: Admin login
	session, err := client.AdminLogin(ctx, baseURL, adminPassword)
	if err != nil {
		return fail(start, errCode(err), "admin_login: %v", err)
	}

	// Step 1: Create token (1 hour)
	token, err := client.CreateToken(ctx, baseURL, session, 3600)
	if err != nil {
		return fail(start, errCode(err), "create_token: %v", err)
	}
	defer func() {
		if err := client.DeleteToken(baseURL, token, session); err != nil {
			slog.Warn("cleanup: delete token failed", "err", err)
		}
	}()

	// Step 2: Bot connects via WebSocket
	bot := client.NewBot(wsURL, token)
	if err := bot.Connect(ctx); err != nil {
		return fail(start, -1, "bot_connect: %v", err)
	}
	defer bot.Close()

	if err := bot.WaitReady(ctx); err != nil {
		return fail(start, -1, "bot_ready: %v", err)
	}

	// Step 3: Chat
	result, err := client.Chat(ctx, baseURL, token, "probe-ping")
	if err != nil {
		return fail(start, errCode(err), "chat: %v", err)
	}

	if result.HasError {
		return fail(start, -1, "chat_error: %s", result.ErrorMsg)
	}
	if !strings.Contains(result.Content, "astron-claw-probe-ok") {
		return fail(start, -1, "unexpected_reply: %s", result.Content)
	}

	return model.ProbeResult{
		Code: 0,
		Info: "success",
		Time: ms(start),
	}
}

// errCode extracts the error code from a CodeError, defaulting to -1.
func errCode(err error) int {
	var ce *model.CodeError
	if errors.As(err, &ce) {
		return ce.ErrCode
	}
	return -1
}

func fail(start time.Time, code int, format string, args ...any) model.ProbeResult {
	return model.ProbeResult{
		Code: code,
		Info: fmt.Sprintf(format, args...),
		Time: ms(start),
	}
}

func ms(start time.Time) int64 {
	return time.Since(start).Milliseconds()
}
