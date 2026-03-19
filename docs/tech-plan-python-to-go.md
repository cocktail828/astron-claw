# Astron Claw 后端 Python → Go 迁移技术方案

## 一、技术方案概述

### 1.1 背景

Astron Claw 是一个 OpenClaw 实时对话桥接服务，当前后端基于 **Python 3.11 + FastAPI + Uvicorn**，总计约 **4,500 行业务代码**（不含测试和迁移脚本）。核心职责：

- 通过 WebSocket 接入 Bot（JSON-RPC 2.0 协议）
- 通过 SSE 向 Chat 客户端推送流式响应
- 通过 Redis Streams 实现跨 Worker 消息路由
- 提供 Token、Session、Admin、Media、Metrics 等 REST API

### 1.2 迁移目标

| 维度 | 目标 |
|------|------|
| **功能完整** | 1:1 覆盖所有现有 API 端点（18 个）及 WebSocket/SSE 行为 |
| **协议兼容** | 前端零改动，插件零改动，所有请求/响应格式保持一致 |
| **性能提升** | 利用 Go 的原生并发降低延迟、提升吞吐 |
| **运维简化** | 单二进制部署，无 Python 虚拟环境依赖 |
| **可维护性** | 清晰的分层架构，强类型保障 |

### 1.3 迁移范围

| 范围 | 说明 |
|------|------|
| **迁移** | `server/` 目录下全部 Python 代码 → `backend/` 目录下 Go 代码 |
| **保留不变** | `web/`（Vue 3 前端）、`plugin/`（OpenClaw 插件）、`probe/`（Go 探针） |
| **更新** | `docker-compose.yml`、`Makefile`、`backend/Dockerfile` |

---

## 二、技术选型

### 2.1 选型总览

| 类别 | Python（现有） | Go（目标） | 选型理由 |
|------|---------------|-----------|---------|
| **Web 框架** | FastAPI + Uvicorn | **Gin** | 高性能、生态成熟、中间件机制完善 |
| **WebSocket** | `websockets` 库 | **gorilla/websocket** | 项目 probe 已在用，事实标准 |
| **SSE** | 手写 StreamingResponse | **Gin StreamingResponse** / `c.Stream()` | Gin 原生支持 SSE |
| **MySQL** | SQLAlchemy + aiomysql | **GORM** | Go 最流行 ORM，支持 AutoMigrate |
| **Redis** | redis-py[hiredis] | **go-redis/redis v9** | 官方推荐，支持 Streams/Cluster |
| **S3 存储** | aiobotocore | **aws-sdk-go-v2** | 官方 SDK，原生支持 |
| **配置** | python-dotenv | **godotenv** + **envconfig** | .env 加载 + struct 映射 |
| **日志** | Loguru | **zerolog** | 结构化 JSON 日志、高性能、零分配 |
| **数据库迁移** | Alembic | **golang-migrate** | 独立迁移工具，支持嵌入 SQL 文件 |
| **OTel 指标** | opentelemetry-sdk | **go.opentelemetry.io/otel** | 官方 SDK |
| **加密** | cryptography | **crypto/sha256** (标准库) | 标准库已足够 |
| **测试** | pytest + pytest-asyncio | **testing** + **testify** | testify 提供 assert/mock/suite |
| **HTTP 客户端** | aiohttp | **net/http** (标准库) | 标准库已足够 |

### 2.2 备选方案对比

| 决策点 | 方案 A（推荐） | 方案 B | 取舍 |
|--------|-------------|--------|------|
| Web 框架 | **Gin** | Echo / Fiber | Gin 社区最大，中间件/文档最丰富 |
| ORM | **GORM** | sqlx (raw SQL) | GORM 开发效率高，迁移方便；sqlx 性能略优但开发成本高 |
| Redis | **go-redis v9** | redigo | go-redis 原生支持 Streams/Cluster，类型安全 |
| 日志 | **zerolog** | zap / slog | zerolog 零分配、API 简洁；slog 标准库但生态尚新 |

---

## 三、架构设计

### 3.1 分层架构

```
┌─────────────────────────────────────────────────────┐
│                   Router 层 (Gin)                    │
│  health / tokens / admin / sse / websocket / media  │
├─────────────────────────────────────────────────────┤
│                Middleware 层                          │
│    CORS │ TokenAuth │ AdminAuth │ Recovery │ Logger  │
├─────────────────────────────────────────────────────┤
│                  Service 层                          │
│  Bridge │ Queue │ SessionStore │ TokenMgr │ AdminAuth│
│               MediaMgr │ Telemetry                   │
├─────────────────────────────────────────────────────┤
│                Infrastructure 层                     │
│  Config │ MySQL/GORM │ Redis │ S3 │ Logger │ Migrate │
└─────────────────────────────────────────────────────┘
```

### 3.2 目录结构

```
backend/
├── cmd/
│   └── server/
│       └── main.go              # 入口：加载配置、初始化依赖、启动 HTTP
├── internal/
│   ├── config/
│   │   └── config.go            # 配置 struct + .env 加载
│   ├── infra/
│   │   ├── database.go          # GORM 初始化 + 自动建库
│   │   ├── cache.go             # go-redis 初始化（standalone/cluster）
│   │   ├── storage/
│   │   │   ├── storage.go       # ObjectStorage 接口
│   │   │   ├── s3.go            # S3/MinIO 实现
│   │   │   └── ifly_gateway.go  # 讯飞网关实现
│   │   ├── migrate.go           # golang-migrate 嵌入 SQL
│   │   └── telemetry/
│   │       ├── metrics.go       # OTel 指标定义
│   │       ├── provider.go      # MeterProvider
│   │       ├── reader.go        # Prometheus 格式渲染
│   │       └── redis_exporter.go # 自定义 Redis 导出器
│   ├── model/
│   │   ├── token.go             # Token GORM 模型
│   │   ├── admin_config.go      # AdminConfig GORM 模型
│   │   ├── chat_session.go      # ChatSession GORM 模型
│   │   └── errors.go            # 错误码枚举
│   ├── service/
│   │   ├── bridge.go            # ConnectionBridge 核心
│   │   ├── queue.go             # MessageQueue 接口 + RedisStreamQueue
│   │   ├── session_store.go     # Session CRUD (MySQL + Redis 缓存)
│   │   ├── token_manager.go     # Token CRUD
│   │   ├── admin_auth.go        # Admin 密码认证 + Session
│   │   └── media_manager.go     # 媒体上传
│   ├── middleware/
│   │   ├── token_auth.go        # Bearer Token 中间件
│   │   ├── admin_auth.go        # Admin Cookie 中间件
│   │   └── cors.go              # CORS 中间件
│   ├── router/
│   │   ├── router.go            # 路由注册总入口
│   │   ├── health.go            # GET /api/health
│   │   ├── tokens.go            # POST /api/token, /api/token/validate
│   │   ├── admin_auth.go        # Admin auth 端点
│   │   ├── admin.go             # Admin token CRUD
│   │   ├── sse.go               # POST /bridge/chat + session 端点
│   │   ├── websocket.go         # WS /bridge/bot
│   │   ├── media.go             # POST /api/media/upload
│   │   └── metrics.go           # GET/DELETE /api/metrics
│   └── pkg/
│       ├── jsonrpc.go           # JSON-RPC 2.0 消息构造
│       └── sse.go               # SSE 格式化工具
├── migrations/
│   ├── 000001_init.up.sql
│   ├── 000001_init.down.sql
│   └── ...                      # 嵌入的 SQL 迁移文件
├── go.mod
├── go.sum
├── Dockerfile
├── .env.example
└── Makefile
```

### 3.3 关键设计决策

| # | 决策 | 选择 | 理由 |
|---|------|------|------|
| 1 | **并发模型** | 每个 WebSocket 连接 2 个 goroutine（read + write）；SSE 用 goroutine + channel | Go goroutine 天然轻量，无需 asyncio 事件循环 |
| 2 | **多 Worker** | **单进程多 goroutine**，去掉 multi-worker | Go 单进程即可充分利用多核，无需 Python 的 prefork 模式 |
| 3 | **消息路由** | 保持 Redis Streams 方案不变 | 已验证的跨实例路由方案，支持水平扩展 |
| 4 | **数据库迁移** | golang-migrate，SQL 文件通过 `embed` 嵌入二进制 | 迁移文件随二进制分发，零外部依赖 |
| 5 | **优雅关闭** | `context.Context` + `signal.NotifyContext` | Go 标准模式，所有 goroutine 通过 ctx 取消 |
| 6 | **依赖注入** | 构造函数传参（无 DI 框架） | 项目规模不大，显式传参更清晰 |
| 7 | **配置管理** | 保持 `.env` 文件格式不变 | 前端/运维零改动 |
| 8 | **Bot 心跳/驱逐** | 保持 Redis ZSET + Generation Counter | 已验证的分布式方案 |

---

## 四、模块划分

### 4.1 模块映射表（Python → Go）

| Python 模块 | Go 模块 | 核心职责 | 行数估算 |
|-------------|---------|---------|---------|
| `server/infra/config.py` (161L) | `backend/internal/config/config.go` | 配置加载 | ~120L |
| `server/infra/database.py` (72L) | `backend/internal/infra/database.go` | GORM 初始化 | ~60L |
| `server/infra/cache.py` (50L) | `backend/internal/infra/cache.go` | Redis 客户端 | ~50L |
| `server/infra/models.py` (53L) | `backend/internal/model/*.go` | ORM 模型 | ~80L |
| `server/infra/errors.py` (100L) | `backend/internal/model/errors.go` | 错误码 | ~100L |
| `server/infra/log.py` (137L) | `backend/internal/infra/logger.go` | zerolog 配置 | ~80L |
| `server/infra/migration.py` (214L) | `backend/internal/infra/migrate.go` | 数据库迁移 | ~60L |
| `server/infra/token_auth.py` (106L) | `backend/internal/middleware/token_auth.go` | Token 中间件 | ~80L |
| `server/infra/storage/*.py` (~280L) | `backend/internal/infra/storage/*.go` | 对象存储 | ~250L |
| `server/infra/telemetry/*.py` (~450L) | `backend/internal/infra/telemetry/*.go` | OTel 指标 | ~350L |
| `server/services/bridge.py` (616L) | `backend/internal/service/bridge.go` | 核心桥接 | ~550L |
| `server/services/queue.py` (242L) | `backend/internal/service/queue.go` | Redis Streams 队列 | ~200L |
| `server/services/session_store.py` (176L) | `backend/internal/service/session_store.go` | Session 存储 | ~150L |
| `server/services/token_manager.py` (140L) | `backend/internal/service/token_manager.go` | Token 管理 | ~120L |
| `server/services/admin_auth.py` (93L) | `backend/internal/service/admin_auth.go` | Admin 认证 | ~90L |
| `server/services/media_manager.py` (94L) | `backend/internal/service/media_manager.go` | 媒体上传 | ~90L |
| `server/routers/sse.py` (360L) | `backend/internal/router/sse.go` | SSE 端点 | ~300L |
| `server/routers/websocket.py` (88L) | `backend/internal/router/websocket.go` | WS 端点 | ~100L |
| `server/routers/*.py` (~280L) | `backend/internal/router/*.go` | 其余 REST 端点 | ~250L |
| `server/app.py` (125L) | `backend/cmd/server/main.go` + `backend/internal/router/router.go` | 应用入口 | ~150L |
| **总计** | | | **~3,230L** |

### 4.2 模块依赖关系

```
cmd/server/main.go
  ├── config       ← 读取 .env
  ├── infra
  │   ├── database ← GORM
  │   ├── cache    ← go-redis
  │   ├── storage  ← S3/iFlytek
  │   ├── migrate  ← golang-migrate
  │   ├── logger   ← zerolog
  │   └── telemetry
  ├── service
  │   ├── bridge   ← queue, cache
  │   ├── queue    ← cache (Redis Streams)
  │   ├── session_store ← database, cache
  │   ├── token_manager ← database
  │   ├── admin_auth    ← database, cache
  │   └── media_manager ← storage
  ├── middleware   ← token_manager, cache
  └── router       ← service, middleware
```

---

## 五、接口设计

所有接口**保持与现有 Python 版完全一致**，确保前端和插件零改动。

### 5.1 API 端点契约（不变）

| 方法 | 路径 | 认证 | 请求 | 响应 |
|------|------|------|------|------|
| `GET` | `/api/health` | 无 | - | `{"code":0,"status":"ok","mysql":true,"redis":true}` |
| `POST` | `/api/token` | 无 | - | `{"code":0,"token":"sk-..."}` |
| `POST` | `/api/token/validate` | 无 | `{"token":"sk-..."}` | `{"code":0,"valid":true,"bot_connected":true}` |
| `GET` | `/api/metrics` | 无 | - | Prometheus text |
| `GET` | `/api/admin/auth/status` | Cookie | - | `{"code":0,"need_setup":bool,"authenticated":bool}` |
| `POST` | `/api/admin/auth/setup` | 无 | `{"password":"..."}` | `{"code":0}` + Set-Cookie |
| `POST` | `/api/admin/auth/login` | 无 | `{"password":"..."}` | `{"code":0}` + Set-Cookie |
| `POST` | `/api/admin/auth/logout` | Cookie | - | `{"code":0}` + Clear-Cookie |
| `GET` | `/api/admin/tokens` | Cookie | Query: page, page_size, search, sort_by, sort_order, bot_status | `{"code":0,"tokens":[...],"total":N,...}` |
| `POST` | `/api/admin/tokens` | Cookie | `{"name":"...","expires_in":N}` | `{"code":0,"token":"sk-..."}` |
| `PATCH` | `/api/admin/tokens/:token` | Cookie | `{"name":"...","expires_in":N}` | `{"code":0}` |
| `DELETE` | `/api/admin/tokens/:token` | Cookie | - | `{"code":0}` |
| `POST` | `/api/admin/cleanup` | Cookie | - | `{"code":0,"removed_tokens":N,"removed_sessions":N}` |
| `POST` | `/api/media/upload` | Bearer | multipart `file` + `sessionId` | `{"code":0,"fileName":"...","downloadUrl":"..."}` |
| `POST` | `/bridge/chat` | Bearer | `{"content":"...","sessionId":"...","media":[...]}` | SSE stream |
| `GET` | `/bridge/chat/sessions` | Bearer | - | `{"code":0,"sessions":[...]}` |
| `POST` | `/bridge/chat/sessions` | Bearer | - | `{"code":0,"sessionId":"...","sessionNumber":N,"sessions":[...]}` |
| `WS` | `/bridge/bot` | Query/Header | - | WebSocket (JSON-RPC 2.0) |

### 5.2 SSE 事件格式（不变）

```
event: session
data: {"sessionId":"uuid","sessionNumber":1}

event: chunk
data: {"content":"Hello"}

event: thinking
data: {"content":"Let me think..."}

event: tool_call
data: {"name":"search","input":"..."}

event: tool_result
data: {"name":"search","status":"success","content":"..."}

event: media
data: {"type":"url","content":"http://...","caption":"..."}

event: done
data: {"content":"Final response"}

event: error
data: {"content":"Error message"}
```

### 5.3 WebSocket JSON-RPC 协议（不变）

**Server → Bot (session/prompt):**
```json
{
  "jsonrpc": "2.0",
  "id": "req_uuid",
  "method": "session/prompt",
  "params": {
    "sessionId": "uuid",
    "prompt": {
      "content": [
        {"type": "text", "content": "user message"},
        {"type": "url", "content": "http://..."}
      ]
    }
  }
}
```

**Bot → Server (session/update):**
```json
{
  "jsonrpc": "2.0",
  "method": "session/update",
  "params": {
    "sessionId": "uuid",
    "update": {
      "sessionUpdate": "agent_message_chunk",
      "content": "delta text"
    }
  }
}
```

### 5.4 错误码（不变）

| 错误码 | HTTP | 含义 |
|--------|------|------|
| `AUTH_INVALID_TOKEN` | 401 | Token 无效 |
| `AUTH_MISSING_AUTH` | 401 | 缺少认证信息 |
| `AUTH_INVALID_SESSION` | 401 | Admin Session 无效 |
| `AUTH_UNAUTHORIZED` | 401 | 未授权 |
| `AUTH_WRONG_PASSWORD` | 401 | 密码错误 |
| `ADMIN_PASSWORD_EXISTS` | 400 | 密码已存在 |
| `ADMIN_PASSWORD_SHORT` | 400 | 密码太短 |
| `CHAT_EMPTY_MESSAGE` | 400 | 空消息 |
| `CHAT_NO_BOT` | 400 | Bot 未连接 |
| `CHAT_SEND_FAILED` | 500 | 消息发送失败 |
| `CHAT_STREAM_TIMEOUT` | SSE | 流超时 |
| `CHAT_INTERNAL_ERROR` | SSE | 内部错误 |
| `MEDIA_FILE_TOO_LARGE` | 413 | 文件超限 (500MB) |
| `MEDIA_INVALID_FILE` | 400 | 无效文件 |
| `MEDIA_BAD_URL_SCHEME` | 400 | 无效 URL 协议 |
| `MEDIA_UNSUPPORTED_TYPE` | 400 | 不支持的文件类型 |
| `WS_INVALID_TOKEN` | 4001 | WebSocket Token 无效 |
| `WS_TOKEN_DELETED` | 4003 | Token 已删除 |
| `WS_SERVER_RESTART` | 4000 | 服务器重启 |
| `WS_EVICTED` | 4005 | 被新连接驱逐 |

---

## 六、数据模型

### 6.1 GORM 模型定义

```go
// Token 表
type Token struct {
    ID        uint      `gorm:"primaryKey;autoIncrement" json:"-"`
    Token     string    `gorm:"type:varchar(64);uniqueIndex;not null" json:"token"`
    Name      string    `gorm:"type:varchar(255);default:''" json:"name"`
    CreatedAt time.Time `gorm:"autoCreateTime" json:"created_at"`
    ExpiresAt time.Time `gorm:"not null" json:"expires_at"`
}

func (Token) TableName() string { return "tokens" }

// AdminConfig 表
type AdminConfig struct {
    ID    uint   `gorm:"primaryKey;autoIncrement"`
    Key   string `gorm:"type:varchar(64);uniqueIndex;not null"`
    Value string `gorm:"type:text;not null"`
}

func (AdminConfig) TableName() string { return "admin_config" }

// ChatSession 表
type ChatSession struct {
    ID            uint      `gorm:"primaryKey;autoIncrement" json:"-"`
    Token         string    `gorm:"type:varchar(64);index;not null" json:"-"`
    SessionID     string    `gorm:"column:session_id;type:varchar(36);uniqueIndex;not null" json:"id"`
    SessionNumber int       `gorm:"column:session_number;not null" json:"number"`
    CreatedAt     time.Time `gorm:"autoCreateTime" json:"created_at"`
}

func (ChatSession) TableName() string { return "chat_sessions" }
```

### 6.2 Redis 数据结构（不变）

| Key Pattern | 类型 | TTL | 用途 |
|-------------|------|-----|------|
| `token_auth:{token}` | String | 30s | Token 验证缓存 |
| `bridge:sessions:{token}` | String (JSON) | 1h | Session 列表缓存 |
| `admin:session:{sessionId}` | String | 24h | Admin 会话 |
| `bridge:bot_alive` | ZSET | - | Bot 心跳时间戳 |
| `bridge:bot_gen:{token}` | String (int) | - | Bot 连接代数 |
| `bridge:bot_inbox:{token}` | Stream | maxlen 1000 | Bot 消息收件箱 |
| `bridge:chat_inbox:{token}:{sid}` | Stream | maxlen 1000 | Chat 消息收件箱 |
| `migrate:lock` | String | 60s | 分布式迁移锁 |
| `bridge:cleanup_lock` | String | 60s | 清理任务锁 |
| `{otlp}:counters` | Hash | - | OTel 计数器 |
| `{otlp}:histograms` | Hash | - | OTel 直方图 |
| `{otlp}:gauges:*` | Hash | - | OTel 仪表盘 |

### 6.3 数据库迁移策略

- 使用 `golang-migrate` 替代 Alembic
- 将现有 6 个 Alembic 迁移合并为 1 个初始迁移 SQL（当前表结构已稳定）
- SQL 文件通过 `//go:embed` 嵌入二进制
- 启动时自动运行，通过 Redis 分布式锁确保只有一个实例执行（复用现有锁机制）

---

## 七、关键设计决策

### 7.1 并发模型：goroutine 替代 asyncio

**Python（现有）：**
```
Uvicorn + uvloop → N worker 进程 → 每进程 1 event loop → async/await 协程
```

**Go（目标）：**
```
单进程 → goroutine per connection → channel 通信
```

| 组件 | Python 实现 | Go 实现 |
|------|------------|---------|
| WebSocket 读循环 | `async for msg in ws` | `goroutine + ws.ReadMessage()` |
| WebSocket 写入 | `await ws.send()` | 通过 `chan []byte` 串行写入 |
| SSE 流 | `async generator + yield` | `goroutine + channel + c.Stream()` |
| Redis Streams 消费 | `await redis.xreadgroup()` | `goroutine + redis.XReadGroup()` |
| 心跳定时器 | `asyncio.create_task()` | `time.Ticker` in goroutine |
| 超时控制 | `asyncio.wait_for()` | `context.WithTimeout()` |

### 7.2 SSE 实现方案

```go
func (h *SSEHandler) Chat(c *gin.Context) {
    // 1. 解析请求 + 验证
    // 2. 发送消息到 bot inbox (Redis Stream)
    // 3. 流式响应
    c.Header("Content-Type", "text/event-stream")
    c.Header("Cache-Control", "no-cache")
    c.Header("Connection", "keep-alive")

    ctx := c.Request.Context()
    ticker := time.NewTicker(15 * time.Second)  // heartbeat
    timeout := time.After(10 * time.Minute)      // stream timeout

    c.Stream(func(w io.Writer) bool {
        select {
        case <-ctx.Done():
            return false
        case <-timeout:
            writeSSE(w, "error", `{"content":"Stream timeout"}`)
            return false
        case <-ticker.C:
            fmt.Fprintf(w, ": heartbeat\n\n")
            return true
        default:
            // XREADGROUP with 1s block
            msgs := queue.Consume(ctx, streamKey, 1*time.Second)
            for _, msg := range msgs {
                writeSSE(w, msg.Event, msg.Data)
                if msg.Event == "done" || msg.Event == "error" {
                    return false
                }
            }
            return true
        }
    })
}
```

### 7.3 WebSocket 连接管理

```go
type BotConnection struct {
    token      string
    ws         *websocket.Conn
    generation int64
    writeCh    chan []byte     // 串行化写入
    ctx        context.Context
    cancel     context.CancelFunc
}

// 每个连接启动 3 个 goroutine：
// 1. readPump：读取 bot 消息 → 处理 → 转发到 chat inbox
// 2. writePump：从 writeCh 读取 → 写入 WebSocket
// 3. heartbeat：定期更新 Redis ZSET 心跳
```

### 7.4 优雅关闭

```go
func main() {
    ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
    defer stop()

    srv := &http.Server{Addr: cfg.Server.Addr(), Handler: router}

    go srv.ListenAndServe()

    <-ctx.Done()
    // 1. 关闭 HTTP 服务（不再接受新请求）
    shutdownCtx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
    defer cancel()
    srv.Shutdown(shutdownCtx)
    // 2. 关闭所有 bot WebSocket（发送 4000 close code）
    bridge.CloseAll(4000, "server restart")
    // 3. 关闭 Redis、MySQL 连接池
    redis.Close()
    db.Close()
}
```

### 7.5 迁移兼容性保障

| 维度 | 保障措施 |
|------|---------|
| **数据库** | 完全复用现有 MySQL 表结构，GORM 模型映射到同一张表 |
| **Redis** | 所有 Key Pattern 保持不变，Go 版与 Python 版可灰度共存 |
| **API** | 所有请求/响应 JSON 字段名使用 `json:"camelCase"` tag 保持一致 |
| **Cookie** | `admin_session` cookie 的 name、path、httpOnly、secure 属性完全一致 |
| **SSE** | 事件格式 `event: xxx\ndata: {...}\n\n` 严格保持 |
| **WebSocket** | JSON-RPC 2.0 消息格式与关闭码完全一致 |

---

## 八、实现计划

### Phase 1：基础骨架（~2 天）

| # | 任务 | 产出 |
|---|------|------|
| 1.1 | 初始化 Go module，创建目录结构 | `go.mod` + 目录树 |
| 1.2 | 实现 `config` 包（.env 加载 + struct 映射） | 配置加载 |
| 1.3 | 实现 `infra/database.go`（GORM 初始化 + 自动建库） | MySQL 连接 |
| 1.4 | 实现 `infra/cache.go`（go-redis standalone/cluster） | Redis 连接 |
| 1.5 | 实现 `infra/logger.go`（zerolog + 文件轮转） | 日志系统 |
| 1.6 | 实现 GORM 模型 + golang-migrate 迁移 | 数据模型 |
| 1.7 | 搭建 Gin 应用 + 健康检查端点 | `GET /api/health` 可用 |

### Phase 2：核心服务（~3 天）

| # | 任务 | 产出 |
|---|------|------|
| 2.1 | 实现 `service/queue.go`（Redis Streams 封装） | 消息队列 |
| 2.2 | 实现 `service/token_manager.go` | Token CRUD |
| 2.3 | 实现 `service/session_store.go`（MySQL + Redis 写穿缓存） | Session 管理 |
| 2.4 | 实现 `service/admin_auth.go`（SHA256 密码 + Redis Session） | Admin 认证 |
| 2.5 | 实现 `service/media_manager.go` + 存储后端 | 媒体上传 |
| 2.6 | 实现 Token Auth 中间件 | 认证中间件 |

### Phase 3：桥接核心（~3 天）

| # | 任务 | 产出 |
|---|------|------|
| 3.1 | 实现 `service/bridge.go` — Bot 注册/驱逐/心跳 | WebSocket 管理 |
| 3.2 | 实现 `router/websocket.go` — WS `/bridge/bot` | Bot 接入 |
| 3.3 | 实现 `router/sse.go` — POST `/bridge/chat` SSE 流 | 聊天流 |
| 3.4 | 实现跨实例消息路由（Redis Streams 消费/发布） | 多实例支持 |
| 3.5 | 实现事件翻译 `_translate_bot_event` Go 版 | 协议转换 |

### Phase 4：REST 端点 + OTel（~2 天）

| # | 任务 | 产出 |
|---|------|------|
| 4.1 | 实现 Token 端点（create, validate） | Token API |
| 4.2 | 实现 Admin 端点（CRUD + cleanup） | Admin API |
| 4.3 | 实现 Media 端点 | 文件上传 API |
| 4.4 | 实现 Metrics 端点（OTel Redis 导出 + Prometheus 渲染） | 监控 |
| 4.5 | 实现 Session 端点（list, create） | Session API |

### Phase 5：测试 + 部署（~2 天）

| # | 任务 | 产出 |
|---|------|------|
| 5.1 | 编写单元测试（对标现有 pytest 用例） | 测试覆盖 |
| 5.2 | 使用现有 `probe/` 工具做端到端验证 | E2E 通过 |
| 5.3 | 更新 Dockerfile（多阶段 Go 构建） | 镜像 |
| 5.4 | 更新 `docker-compose.yml` + `Makefile` | 部署配置 |
| 5.5 | 灰度切换验证（Go 版与前端/插件联调） | 上线 |

### 预估总工期：~12 天

---

## 九、联动更新

迁移完成后需同步更新以下文件：

| 文件 | 变更内容 |
|------|---------|
| 根 `docker-compose.yml` | `dockerfile: server/Dockerfile` → `dockerfile: backend/Dockerfile`，context 更新 |
| 根 `Makefile` | `cd server` → `cd backend`，`uv sync` → `go build`，更新 dev-server 命令 |
| 根 `.gitignore` | 移除 Python 相关项（`__pycache__`、`.venv`），增加 Go 二进制（`/backend/astron-claw`） |
| `web/Dockerfile` / `web/nginx.conf` | 无变化（后端端口和路径不变） |

原 `server/` 目录在迁移完成、E2E 验证通过后删除。

---

## 十、风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| SSE 行为差异（Gin flush 时机） | 前端感知延迟变化 | 使用 `c.Writer.Flush()` 逐事件刷新 |
| WebSocket 关闭码兼容性 | 插件重连逻辑异常 | 严格匹配 4000/4001/4003/4005 码 |
| GORM 生成 SQL 与现有表不一致 | 启动失败 | 使用 AutoMigrate 对齐 + 人工审核 SQL |
| Redis Streams 消费组语义差异 | 消息丢失/重复 | 复用相同 consumer group name，保持 XACK 语义 |
| Go JSON 序列化字段顺序不同 | 不影响功能，但可能影响日志对比 | 忽略（JSON 规范不保证字段顺序） |
