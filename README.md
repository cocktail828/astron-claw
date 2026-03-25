# Astron Claw

[![Release](https://img.shields.io/github/v/release/hygao1024/astron-claw)](https://github.com/hygao1024/astron-claw/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Go](https://img.shields.io/badge/Go-1.26-00ADD8)](https://go.dev/)
[![Vue 3](https://img.shields.io/badge/Vue-3-4FC08D)](web/)
[![TypeScript](https://img.shields.io/badge/TypeScript-Plugin-3178C6)](plugin/)

OpenClaw 实时对话桥接服务 —— 让 OpenClaw Bot 即刻拥有 Web 聊天能力。

Bot 通过 WebSocket 出站连接，Chat 通过 HTTP SSE 接入，服务器按 Token 配对并双向转发消息流。无需为 Bot 分配公网 IP，部署即可用。

```
Chat Client ──HTTP SSE──▶ ┌─────────────────────┐ ◀──WebSocket── Bot Plugin (OpenClaw)
           /bridge/chat   │                     │  /bridge/bot
                          │   Go Backend        │
                          │    Gin  + Redis     │
                          │                     │
                          │  Token Pairing      │
                          │  Session Routing    │
                          │  Media Relay        │
                          └─────────┬───────────┘
                                    │
                          ┌─────────┴─────────┐
                          │  MySQL     Redis   │
                          │  Storage   Cache   │
                          │            Inbox   │
                          └───────────────────┘
```

## Features

**核心能力**

- **Token 配对路由** — 1 Token = 1 Bot + N Chat，自动配对，开箱即用
- **真流式传输** — SSE 逐 token 推送，支持 thinking / text / tool_call / tool_result / media 等事件类型
- **多媒体双向传输** — 图片、音频、视频、文件上传下载，S3/MinIO 持久存储
- **多会话管理** — 同一 Token 下创建多个独立对话，支持会话切换与固定

**工程质量**

- **多实例高可用** — Redis Streams 跨实例消息路由，连接状态全局可见，水平扩展无忧
- **优雅关闭 & 自动重连** — 滚动更新零中断，Chat/Bot 自动恢复会话
- **链路追踪日志** — 每条日志携带 token + session_id，`grep` 即可端到端排查
- **分布式自动迁移** — 启动时 Redis 分布式锁保障仅一个实例执行数据库迁移

**开箱即用**

- **Vue 3 SPA 前端** — Chat 聊天 + Admin 管理 + Metrics 监控，前后端分离，支持独立部署（Nginx / CDN）
- **Admin 管理面板** — Token CRUD、在线状态监控、过期清理，密码认证保护
- **一行命令装插件** — `curl | bash` 从 GitHub Release 自动下载安装
- **Docker 部署** — 多阶段构建，启动自动迁移，单二进制部署，内置健康检查

## Quick Start

### 1. 安装依赖

```bash
make install
# 等价于: cd web && pnpm install && cd ../backend && go mod download
```

### 2. 配置环境

```bash
cd backend
cp .env.example .env    # 编辑 .env，填写 MySQL / Redis / OSS 连接信息
```

> 数据库迁移会在服务启动时自动执行，无需手动操作。

### 3. 一键启动（前端 + 后端）

```bash
# 开发模式（前端热更新）
make dev

# 生产预览模式（先构建再启动，远程访问更快）
make preview
```

启动后访问：

| 地址 | 说明 |
|------|------|
| `http://localhost:5173/` | Chat 聊天界面 |
| `http://localhost:5173/admin` | Admin 管理面板（首次需设置密码） |
| `http://localhost:5173/metrics` | Metrics 监控面板 |
| `http://localhost:8765/api/health` | 后端健康检查 |

> 前端默认监听 5173 端口，后端 API 监听 8765 端口。前端通过 CORS 跨域调用后端 API。

### 4. 安装 Bot 插件

在 Go backend 已经部署并可访问后，在 Admin 面板创建 Token，然后在 Bot 所在机器一行安装：

```bash
curl -fsSL https://raw.githubusercontent.com/hygao1024/astron-claw/main/install.sh | bash -s -- \
  --bot-token <token> --server-url ws://<server-ip>:8765/bridge/bot
```

<details>
<summary>安装参数说明</summary>

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--bot-token` | Admin 面板生成的 Token | 必填 |
| `--server-url` | Bridge WebSocket 地址 | `ws://localhost:8765/bridge/bot` |
| `--target-dir` | 插件安装目录 | `~/.openclaw/extensions/astron-claw` |
| `--version` | Release 版本标签 | `latest` |

</details>

### 5. Docker 部署（可选）

```bash
# 使用 docker compose（前端 + 后端）
docker compose up -d

# 或者单独构建后端镜像
docker build -f backend/Dockerfile -t astron-claw-backend .

docker run -d --name astron-claw-backend -p 8765:8765 \
  -e MYSQL_HOST=<mysql-ip> \
  -e MYSQL_PASSWORD=<password> \
  -e REDIS_ADDRS=<redis-ip>:6379 \
  astron-claw-backend
```

容器启动时自动执行数据库迁移，内置健康检查（30s 间隔）。

## Configuration

<details>
<summary>环境变量一览</summary>

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `MYSQL_HOST` | MySQL 地址 | `127.0.0.1` |
| `MYSQL_PORT` | MySQL 端口 | `3306` |
| `MYSQL_USER` | MySQL 用户名 | `root` |
| `MYSQL_PASSWORD` | MySQL 密码 | — |
| `MYSQL_DATABASE` | 数据库名 | `astron_claw` |
| `REDIS_ADDRS` | Redis 节点地址（逗号分隔，多地址自动启用集群模式） | `127.0.0.1:6379` |
| `REDIS_CLUSTER` | 是否强制使用 Redis Cluster 客户端（单 seed 节点接入集群时设为 `true`） | `false` |
| `REDIS_PASSWORD` | Redis 密码 | — |
| `REDIS_DB` | Redis DB 编号（集群模式忽略） | `0` |
| `SERVER_HOST` | 监听地址 | `0.0.0.0` |
| `SERVER_PORT` | 监听端口 | `8765` |
| `SERVER_LOG_LEVEL` | 日志级别 | `info` |
| `SERVER_ACCESS_LOG` | 访问日志 | `true` |
| `COOKIE_SECURE` | Cookie Secure 标志（HTTPS 时设为 true） | `false` |
| `CORS_ENABLED` | 是否启用 CORS 中间件 | `true` |
| `CORS_ORIGINS` | 允许的跨域来源（逗号分隔） | `*` |

</details>

## API

详细文档见 [docs/api.md](docs/api.md)。

| 端点 | 说明 |
|------|------|
| `POST /bridge/chat` | Chat 对话（SSE 流式响应） |
| `WS /bridge/bot` | Bot 连接（JSON-RPC 2.0） |
| `*/bridge/chat/sessions` | 会话管理（创建 / 列表） |
| `*/api/token` | Token 创建 & 验证 |
| `*/api/admin/*` | Admin 认证 & Token CRUD & 清理 |
| `*/api/media/*` | 媒体上传 / 下载 |
| `GET /api/metrics` | Prometheus 指标 |
| `GET /api/health` | 健康检查 |

## Architecture

```
web/                # Vue 3 SPA 前端（Chat + Admin + Metrics）
├── src/views/      # 页面：ChatView / AdminView / MetricsView
├── src/components/ # 组件：MessageBubble / ToolCallCard / SessionSidebar
├── src/stores/     # Pinia 状态管理：chat / admin / metrics
├── src/composables/# 组合式函数：useTheme / consumeSSE
└── src/api/        # API 客户端：Axios 封装

backend/            # Go (Gin) 后端
├── cmd/server/     # 入口：main.go
├── internal/
│   ├── config/     # 配置加载（.env）
│   ├── infra/      # 基础设施：MySQL / Redis / S3 / Log / Migration / Telemetry
│   ├── model/      # GORM 数据模型 + 错误码
│   ├── service/    # 业务逻辑：Bridge / Queue / Session / Token / Media / Auth
│   ├── middleware/  # 中间件：TokenAuth / CORS
│   ├── router/     # 路由层：SSE / WebSocket / REST API
│   └── pkg/        # 工具包：SSE 格式化 / JSON-RPC
└── migrations/     # SQL 数据库迁移（编译时嵌入）

plugin/             # OpenClaw Channel Plugin (TypeScript)
├── src/bridge/     # WebSocket 传输层 + 媒体 API + 连接监控
└── src/messaging/  # 入站/出站消息处理 + 类型策略
```

## Development

```bash
# 一键启动前后端（开发模式）
make dev

# 一键构建 + 预览（生产模式）
make preview

# 仅启动后端
make dev-server

# 仅启动前端
make dev-web

# 构建前端
make build-web

# 后端单元测试
cd backend && go test ./...

# 端到端探针测试（需先启动服务）
cd probe && go run . <server-addr> <admin-password>
```

## Tech Stack

| 层级 | 技术 |
|------|------|
| Server | Go 1.26 / Gin / gorilla/websocket |
| Storage | MySQL (GORM) / Redis Streams / S3 |
| Plugin | TypeScript / WebSocket / OpenClaw SDK |
| Frontend | Vue 3 / TypeScript / Pinia / Vue Router / Naive UI / Vite |
| Protocol | HTTP SSE + WebSocket + JSON-RPC 2.0 |
| Deploy | Docker Multi-stage / Single Binary / pnpm / golang-migrate |

## License

[MIT](LICENSE)
