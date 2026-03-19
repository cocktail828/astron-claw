# Astron Claw 拨测工具（Probe）技术方案

## 1. 技术方案概述

构建一个独立的 Go 语言拨测工具，覆盖 Astron Claw 桥接服务的核心链路：**Admin 登录 → 创建 Token → Bot WebSocket 连接 → Chat 对话 → 验证 Bot 响应 → 删除 Token**。任意环节出错即判定失败，最终输出统一格式的成功/失败响应体。

### CLI 使用方式

```
./probe <domain> <admin_password>

# 示例
./probe example.com admin123
```

- 两个位置参数：`domain`（自动拼接 `http://{domain}` 和 `ws://{domain}`）、`admin_password`
- 编译为单二进制，零运行时依赖

### 测试流程时序图

```
Probe Runner
  │
  ├─ 0. POST /api/admin/auth/login            → Admin 登录，获取 session cookie
  │
  ├─ 1. POST /api/admin/tokens                → 创建临时 Token (expires_in=3600)
  │      Cookie: admin_session=xxx
  │      Body: {"name":"probe-test","expires_in":3600}
  │
  ├─ 2. WS  /bridge/bot?token=sk-xxx          → 模拟 Bot 建立 WebSocket 连接
  │      └─ 启动 goroutine 监听 session/prompt
  │         收到请求后回复固定字样 "astron-claw-probe-ok"
  │
  ├─ 3. POST /bridge/chat                     → 发送对话消息，读取 SSE 流
  │      Authorization: Bearer sk-xxx
  │      Body: {"content": "probe-ping"}
  │      └─ 解析 SSE 事件，验证:
  │           - 收到 session 事件
  │           - 收到 chunk/done 事件，内容包含固定字样
  │           - 未收到 error 事件
  │
  ├─ 4. 关闭 Bot WebSocket 连接
  │
  └─ 5. DELETE /api/admin/tokens/{token}       → 清理 Token
         Cookie: admin_session=xxx

  输出:
  {"code": 0, "info": "success", "time": 1523}                          ← 成功
  {"code": 1, "info": "bot_connect: ws close 4001", "time": 823}        ← 失败
```

## 2. 技术选型

| 项目 | 选型 | 理由 |
|------|------|------|
| 语言 | Go 1.21+ | 用户指定；原生并发、编译为单二进制易部署 |
| WebSocket 客户端 | `gorilla/websocket` | Go 生态最成熟的 WS 库，API 简洁 |
| HTTP 客户端 | `net/http`（标准库） | SSE 解析场景无需第三方库，逐行读取即可 |
| JSON 处理 | `encoding/json`（标准库） | 够用，无需引入第三方 |
| 配置管理 | 位置参数（`os.Args`） | 拨测工具保持轻量，仅需域名和密码 |
| 日志 | `log/slog`（Go 1.21 标准库） | 结构化日志，零依赖 |

**唯一的外部依赖：`github.com/gorilla/websocket`**

## 3. 架构设计

### 3.1 项目位置

```
astron-claw/
├── server/      # Python 后端
├── web/         # Vue 前端
├── plugin/      # TS 插件
├── probe/       # Go 拨测工具（新增）
├── docs/
├── docker-compose.yml
├── Makefile
└── ...
```

### 3.2 模块结构

```
probe/
├── go.mod                    # module: github.com/hygao1024/astron-claw/probe
├── go.sum
├── main.go                   # 入口：参数解析、流程编排、JSON 输出
├── client/
│   ├── http.go               # AdminLogin, CreateToken, DeleteToken
│   ├── sse.go                # Chat (SSE 流解析)
│   └── bot.go                # Bot WebSocket 模拟器
└── model/
    └── types.go              # 所有数据结构
```

### 3.3 模块划分

| 模块 | 职责 | 对外接口 |
|------|------|---------|
| `main` | 流程编排、计时、错误聚合、最终输出 | CLI 入口 |
| `client/http` | Token 创建/删除、Admin 登录 | `CreateToken()`, `DeleteToken()`, `AdminLogin()` |
| `client/sse` | 发起 Chat 请求、解析 SSE 流、收集事件 | `Chat()` → `ChatResult` |
| `client/bot` | WS 连接、监听 `session/prompt`、回复固定字样 | `Connect()`, `Close()`, `WaitReady()` |
| `model/types` | 数据结构定义 | 各种 struct |

### 3.4 关键设计决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| Admin 认证方式 | CLI 传入 admin 密码，程序自动 login 获取 session | 删除 Token 需要 Admin Cookie，拨测必须无人值守 |
| URL 拼接 | 从 domain 自动拼接 `http://{domain}` 和 `ws://{domain}` | 简化输入，拨测场景固定协议 |
| Bot 回复策略 | 固定回复 `"astron-claw-probe-ok"` | 简单确定性，易于校验 |
| SSE 校验策略 | 验证：1) 有 session 事件 2) done 事件内容含固定字样 3) 无 error 事件 | 覆盖核心路径，不过度校验 |
| 超时控制 | 总超时 60s | 防止某一步卡死导致整个拨测挂起 |
| Token 清理 | 使用 `DELETE /api/admin/tokens/{token}` | defer 中执行，无论成败都清理，避免残留 Token 污染环境 |

## 4. 接口设计

### 4.1 内部模块接口

```go
// client/http.go
func AdminLogin(baseURL, password string) (sessionCookie string, err error)
func CreateToken(baseURL, sessionCookie string, expiresIn int) (token string, err error)
func DeleteToken(baseURL, token, sessionCookie string) error

// client/bot.go
type Bot struct { ... }
func NewBot(wsURL, token string) *Bot
func (b *Bot) Connect(ctx context.Context) error       // 建立 WS 连接
func (b *Bot) WaitReady(ctx context.Context) error      // 等待连接就绪
func (b *Bot) Close() error                             // 优雅关闭

// client/sse.go
type ChatResult struct {
    SessionID string
    Content   string   // done 事件的完整内容
    Events    []SSEEvent
    HasError  bool
    ErrorMsg  string
}
func Chat(ctx context.Context, baseURL, token, message string) (*ChatResult, error)
```

### 4.2 调用的外部 API

| 步骤 | Method | Path | 认证 | 请求体 | 关键响应字段 |
|------|--------|------|------|--------|-------------|
| Admin 登录 | `POST` | `/api/admin/auth/login` | 无 | `{"password":"xxx"}` | `Set-Cookie: admin_session` |
| 创建 Token | `POST` | `/api/admin/tokens` | Admin Cookie | `{"name":"probe-test","expires_in":3600}` | `token` |
| Bot 连接 | WS | `/bridge/bot?token=sk-xxx` | Query token | — | WS 连接成功 |
| Chat 对话 | `POST` | `/bridge/chat` | `Bearer sk-xxx` | `{"content":"probe-ping"}` | SSE 事件流 |
| 删除 Token | `DELETE` | `/api/admin/tokens/{token}` | Admin Cookie | — | `{"code":0}` |

## 5. 数据模型

```go
// model/types.go

// ---- 拨测结果（最终输出） ----
type TestResult struct {
    Code int    `json:"code"` // 0=成功, 非0=失败
    Info string `json:"info"` // 描述信息
    Time int64  `json:"time"` // 耗时(ms)
}

// ---- SSE 事件 ----
type SSEEvent struct {
    Type string          // event type: session, chunk, thinking, done, error...
    Data json.RawMessage // raw JSON data
}

type SessionEvent struct {
    SessionID     string `json:"sessionId"`
    SessionNumber int    `json:"sessionNumber"`
}

type ContentEvent struct {
    Content string `json:"content"`
}

// ---- JSON-RPC (Bot WebSocket) ----
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

// Bot 发送的流式更新 (Notification)
type SessionUpdate struct {
    Method string              `json:"method"` // "session/update"
    Params SessionUpdateParams `json:"params"`
}

type SessionUpdateParams struct {
    Update UpdatePayload `json:"update"`
}

type UpdatePayload struct {
    SessionUpdate string       `json:"sessionUpdate"`
    Content       *TextContent `json:"content,omitempty"`
}

type TextContent struct {
    Type string `json:"type"` // "text"
    Text string `json:"text"`
}

// Bot 发送的完成响应 (Response)
type JSONRPCResponse struct {
    JSONRPC   string      `json:"jsonrpc"`
    ID        string      `json:"id"`
    SessionID string      `json:"sessionId"`
    Result    *StopResult `json:"result,omitempty"`
}

type StopResult struct {
    StopReason string `json:"stopReason"` // "end_turn"
}

// ---- HTTP 响应 ----
type TokenResponse struct {
    Token string `json:"token"`
}

type CodeResponse struct {
    Code  int    `json:"code"`
    Error string `json:"error,omitempty"`
}
```

## 6. 核心流程（main.go）

```go
func run(domain, adminPassword string) TestResult {
    start := time.Now()
    baseURL := "http://" + domain
    wsURL := "ws://" + domain
    ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
    defer cancel()

    // Step 0: Admin 登录
    session, err := client.AdminLogin(baseURL, adminPassword)
    if err != nil {
        return fail(start, "admin_login: %v", err)
    }

    // Step 1: 创建 Token（有效期 1 小时）
    token, err := client.CreateToken(baseURL, session, 3600)
    if err != nil {
        return fail(start, "create_token: %v", err)
    }
    defer client.DeleteToken(baseURL, token, session) // Step 5: 无论成败都清理

    // Step 2: 模拟 Bot 通过 WebSocket 连接
    bot := client.NewBot(wsURL, token)
    if err := bot.Connect(ctx); err != nil {
        return fail(start, "bot_connect: %v", err)
    }
    defer bot.Close()

    if err := bot.WaitReady(ctx); err != nil {
        return fail(start, "bot_ready: %v", err)
    }

    // Step 3: Chat 对话
    result, err := client.Chat(ctx, baseURL, token, "probe-ping")
    if err != nil {
        return fail(start, "chat: %v", err)
    }

    if result.HasError {
        return fail(start, "chat_error: %s", result.ErrorMsg)
    }
    if !strings.Contains(result.Content, "astron-claw-probe-ok") {
        return fail(start, "unexpected_reply: %s", result.Content)
    }

    return TestResult{Code: 0, Info: "success", Time: ms(start)}
}
```

## 7. Bot 模拟器核心逻辑

```go
func (b *Bot) handlePrompt(req JSONRPCRequest) {
    reply := "astron-claw-probe-ok"

    // 1. 发送 agent_message_chunk
    b.send(SessionUpdate{
        Method: "session/update",
        Params: SessionUpdateParams{
            Update: UpdatePayload{
                SessionUpdate: "agent_message_chunk",
                Content:       &TextContent{Type: "text", Text: reply},
            },
        },
    })

    // 2. 发送 agent_message_final (产生 done 事件)
    b.send(SessionUpdate{
        Method: "session/update",
        Params: SessionUpdateParams{
            Update: UpdatePayload{
                SessionUpdate: "agent_message_final",
                Content:       &TextContent{Type: "text", Text: reply},
            },
        },
    })

    // 3. 发送 JSON-RPC Response (完成信号)
    b.send(JSONRPCResponse{
        JSONRPC:   "2.0",
        ID:        req.ID,
        SessionID: req.Params.SessionID,
        Result:    &StopResult{StopReason: "end_turn"},
    })
}
```

## 8. 错误判定规则

| 条件 | 判定 | info 示例 |
|------|------|----------|
| HTTP 状态码 != 200 | 失败 | `"create_token: http 401"` |
| 响应体 `code` != 0 | 失败 | `"delete_token: code=404, Token not found"` |
| SSE 收到 `error` 事件 | 失败 | `"chat_error: Bot timeout"` |
| WebSocket 连接失败 | 失败 | `"bot_connect: ws close 4001"` |
| 任意步骤超时 | 失败 | `"chat: context deadline exceeded"` |
| done 内容不含固定字样 | 失败 | `"unexpected_reply: ..."` |

## 9. 实现计划

| 阶段 | 内容 | 文件 |
|------|------|------|
| P1 | 项目初始化：`go mod init`、目录结构、`model/types.go` | 2 |
| P2 | HTTP 客户端：Admin 登录、Token 创建/删除 | 1 |
| P3 | Bot 模拟器：WebSocket 连接、JSON-RPC 监听与回复 | 1 |
| P4 | SSE 客户端：Chat 请求、事件流解析与校验 | 1 |
| P5 | 主流程编排：`main.go` 串联全流程、计时、输出 | 1 |
| P6 | 集成验证：对接真实服务拨测 | — |
