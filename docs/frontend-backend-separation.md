# Astron Claw 前后端分离技术方案

## 一、技术方案概述

### 1.1 现状分析

| 维度 | 现状 |
|---|---|
| **前端** | 3 个单体 HTML 文件（`index.html` 2867行、`admin.html` 1611行、`metrics.html` 961行），内联 CSS + JS，无构建流程 |
| **后端** | FastAPI (Python)，已具备完整的 REST API + SSE + WebSocket |
| **耦合点** | 后端通过 `HTMLResponse` 直接读取并返回前端 HTML 文件；前端通过 `/static` mount 获取静态资源 |
| **数据交互** | 前端已全部使用 `fetch()` 调用 JSON API，**无服务端模板渲染** |
| **认证** | Token (Bearer) + Admin Cookie Session，均通过 HTTP Header/Cookie 传递 |

### 1.2 改造目标

1. 前端独立工程化：引入现代前端框架与构建工具，支持组件化开发
2. 后端纯 API 化：移除所有 HTML 服务路由，专注于 API 提供
3. 独立部署：前端可独立构建、部署至 CDN/Nginx，后端独立容器化部署
4. 开发体验提升：前端热更新（HMR）、TypeScript 类型安全、组件复用

### 1.3 改造优势

当前架构天然适合分离——**零服务端模板**、**API 边界清晰**、**数据交互全部通过 fetch**。改造成本主要集中在前端工程重构，后端变更极小。

---

## 二、技术选型

### 2.1 前端技术选型

| 技术 | 选型 | 理由 |
|---|---|---|
| **框架** | **Vue 3 (Composition API)** | 学习曲线平缓、生态成熟、适合中后台场景；项目为管理+聊天界面，Vue 的响应式系统天然适合 |
| **构建工具** | **Vite** | 原生 ESM、极速 HMR、Vue 官方推荐；与 Vue 3 一等集成 |
| **语言** | **TypeScript** | 类型安全、接口契约可在前后端共享；项目 plugin 已使用 TS |
| **UI 组件库** | **Naive UI** | 纯 Vue 3 组件库、TypeScript 编写、Tree-shaking 友好、深色主题原生支持 |
| **路由** | **Vue Router 4** | SPA 路由，支持路由守卫做认证拦截 |
| **状态管理** | **Pinia** | Vue 官方推荐、轻量、TypeScript 友好 |
| **HTTP 客户端** | **Axios** | 拦截器机制适合统一 Token 注入和错误处理 |
| **Markdown 渲染** | **markdown-it** + **highlight.js** | 延续现有 Markdown 渲染能力 |
| **包管理** | **pnpm** | 快速、磁盘高效、monorepo 友好 |

### 2.2 备选对比

| 维度 | Vue 3 + Vite | React + Vite | Svelte + Vite |
|---|---|---|---|
| 学习成本 | 低 | 中 | 低 |
| 生态丰富度 | 高 | 最高 | 中 |
| 中后台组件库 | Naive UI / Element Plus | Ant Design / MUI | 较少 |
| Bundle 体积 | 中 | 中 | 最小 |
| 与现有代码风格 | 当前 HTML+JS 迁移自然 | JSX 风格差异大 | 模板语法相似 |
| **推荐** | **首选** | 备选 | 不推荐 |

### 2.3 后端变更

| 技术 | 变更 | 理由 |
|---|---|---|
| **CORS** | 新增 `CORSMiddleware` | 前后端不同源时必须 |
| **静态文件服务** | 移除 `/static` mount 和 HTML 路由 | 前端由 Nginx/CDN 提供 |
| **其余** | 无变更 | 现有 API 已完全满足需求 |

---

## 三、架构设计

### 3.1 分层架构

```
┌──────────────────────────────────────────────────────────────┐
│                      用户浏览器                               │
└──────────────┬───────────────────────────────┬───────────────┘
               │                               │
          (静态资源)                       (API 请求)
               │                               │
┌──────────────▼──────────┐   ┌────────────────▼──────────────┐
│    前端 (Nginx / CDN)    │   │   后端 (FastAPI API Server)    │
│                          │   │                                │
│  Vue 3 SPA              │   │  /api/*    → REST JSON         │
│  Vite 构建产物           │   │  /bridge/* → SSE + WebSocket   │
│  index.html + JS + CSS  │   │  /metrics  → Prometheus        │
└──────────────────────────┘   └───────┬────────────┬──────────┘
                                       │            │
                                ┌──────▼──┐  ┌──────▼──┐
                                │  MySQL  │  │  Redis  │
                                └─────────┘  └─────────┘
```

### 3.2 部署拓扑

```
                    ┌─────────────┐
                    │   Nginx     │
                    │  (反向代理)  │
                    └──────┬──────┘
                           │
            ┌──────────────┼──────────────┐
            │              │              │
     /  (前端SPA)    /api/* (后端)   /bridge/* (后端)
            │              │              │
    ┌───────▼──────┐ ┌─────▼─────────────▼──┐
    │  前端静态资源  │ │   FastAPI Container   │
    │  (dist/)     │ │   (可多实例)           │
    └──────────────┘ └──────────────────────┘
```

**Nginx 负责：**
- `/` → 前端静态资源（SPA fallback 到 `index.html`）
- `/api/*` → 反向代理到 FastAPI
- `/bridge/*` → 反向代理到 FastAPI（SSE + WebSocket）
- `/metrics` → 反向代理到 FastAPI

### 3.3 开发模式

```
前端 (localhost:5173, Vite dev server)
    ↓ proxy
后端 (localhost:8765, Uvicorn)
```

Vite 内置 proxy 将 `/api/*`、`/bridge/*` 转发到后端，开发时无需 CORS。

---

## 四、模块划分

### 4.1 前端模块

```
web/                          # 前端工程根目录
├── public/                   # 静态资源
│   └── astron_logo.png
├── src/
│   ├── api/                  # API 层（Axios 封装）
│   │   ├── client.ts         # Axios 实例、拦截器、错误处理
│   │   ├── token.ts          # Token 相关 API
│   │   ├── admin.ts          # Admin 管理 API
│   │   ├── chat.ts           # Chat SSE API
│   │   ├── media.ts          # 文件上传 API
│   │   └── metrics.ts        # Metrics API
│   ├── views/                # 页面级组件
│   │   ├── ChatView.vue      # 聊天页（对应 index.html）
│   │   ├── AdminView.vue     # 管理页（对应 admin.html）
│   │   └── MetricsView.vue   # 指标页（对应 metrics.html）
│   ├── components/           # 可复用组件
│   │   ├── chat/
│   │   │   ├── MessageBubble.vue
│   │   │   ├── ChatInput.vue
│   │   │   ├── SessionSidebar.vue
│   │   │   ├── ToolCallCard.vue
│   │   │   └── ThinkingBlock.vue
│   │   ├── admin/
│   │   │   ├── TokenTable.vue
│   │   │   ├── TokenFormModal.vue
│   │   │   └── LoginForm.vue
│   │   ├── metrics/
│   │   │   └── MetricsChart.vue
│   │   └── common/
│   │       ├── AppHeader.vue
│   │       └── ThemeToggle.vue
│   ├── composables/          # 组合式函数
│   │   ├── useSSE.ts         # SSE 流式响应处理
│   │   ├── useAuth.ts        # Token 认证状态
│   │   ├── useAdminAuth.ts   # Admin 认证状态
│   │   └── useTheme.ts       # 深浅主题切换
│   ├── stores/               # Pinia 状态管理
│   │   ├── chat.ts           # 聊天消息、会话状态
│   │   ├── auth.ts           # Token 认证状态
│   │   └── admin.ts          # Admin 认证 + Token 管理
│   ├── router/
│   │   └── index.ts          # 路由配置 + 守卫
│   ├── types/                # TypeScript 类型定义
│   │   └── index.ts          # API 响应类型、消息类型等
│   ├── styles/               # 全局样式
│   │   ├── variables.css     # CSS 变量（从现有 HTML 提取）
│   │   └── global.css        # Reset + 公共样式
│   ├── App.vue               # 根组件
│   └── main.ts               # 入口
├── index.html                # Vite 入口 HTML
├── vite.config.ts            # Vite 配置（含 proxy）
├── tsconfig.json
├── package.json
└── .env.development          # 开发环境变量
```

### 4.2 模块职责与依赖

| 模块 | 职责 | 依赖 |
|---|---|---|
| `api/` | HTTP 请求封装、错误统一处理 | `axios`, `types/` |
| `views/` | 页面布局与业务编排 | `components/`, `stores/`, `composables/` |
| `components/` | UI 组件渲染 | `naive-ui`, `stores/` |
| `composables/` | 可复用业务逻辑 | `api/`, `stores/` |
| `stores/` | 全局状态管理 | `api/`, `types/` |
| `router/` | 路由与导航守卫 | `stores/` |
| `types/` | 类型定义 | 无（纯类型） |
| `styles/` | 全局样式 | 无 |

---

## 五、接口设计

### 5.1 现有 API 接口清单（无需变更）

后端已有的 API 完全满足前端需求，**不需要新增或修改接口**。

#### Token 认证

| 方法 | 路径 | 说明 | 认证 |
|---|---|---|---|
| `POST` | `/api/token` | 创建新 Token | 无 |
| `POST` | `/api/token/validate` | 验证 Token 有效性 | 无 |

**请求/响应示例 — 创建 Token：**
```
POST /api/token
→ { "code": 0, "token": "sk-..." }
```

**验证 Token：**
```
POST /api/token/validate
← { "token": "sk-..." }
→ { "code": 0, "valid": true, "bot_connected": true }
```

#### Admin 认证

| 方法 | 路径 | 说明 | 认证 |
|---|---|---|---|
| `GET` | `/api/admin/auth/status` | 检查密码是否已设置 | 无 |
| `POST` | `/api/admin/auth/setup` | 首次设置密码 | 无 |
| `POST` | `/api/admin/auth/login` | Admin 登录 | 无 |
| `POST` | `/api/admin/auth/logout` | Admin 登出 | Cookie |

#### Admin Token 管理

| 方法 | 路径 | 说明 | 认证 |
|---|---|---|---|
| `GET` | `/api/admin/tokens` | 分页查询 Token 列表 | Cookie |
| `POST` | `/api/admin/tokens` | 创建 Token | Cookie |
| `DELETE` | `/api/admin/tokens/{token}` | 删除 Token | Cookie |
| `PATCH` | `/api/admin/tokens/{token}` | 更新 Token | Cookie |
| `POST` | `/api/admin/cleanup` | 清理过期 Token | Cookie |

#### Chat (SSE)

| 方法 | 路径 | 说明 | 认证 |
|---|---|---|---|
| `POST` | `/bridge/chat` | SSE 流式聊天 | Bearer Token |
| `GET` | `/bridge/chat/sessions` | 获取会话列表 | Bearer Token |
| `POST` | `/bridge/chat/sessions` | 创建新会话 | Bearer Token |

#### 其他

| 方法 | 路径 | 说明 | 认证 |
|---|---|---|---|
| `POST` | `/api/media/upload` | 文件上传 | Bearer Token |
| `GET` | `/api/health` | 健康检查 | 无 |
| `GET` | `/api/metrics` | Prometheus 指标 | 无 |
| `DELETE` | `/api/metrics` | 重置指标 | Bearer (Admin Session) |

### 5.2 错误响应格式（已统一）

```json
{
  "code": 401,
  "error": "Invalid or missing token"
}
```

错误码复用 `server/infra/errors.py` 中的 `Err` 枚举，前端按 `code` 字段统一处理。

### 5.3 需要后端新增的唯一变更 — CORS

```python
# server/app.py 新增
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.cors_origins,  # 从环境变量读取
    allow_credentials=True,             # 支持 Cookie 传递
    allow_methods=["*"],
    allow_headers=["*"],
)
```

---

## 六、数据模型

### 6.1 后端数据模型（无变更）

现有数据模型完全满足需求，**不需要任何变更**：

```
tokens              admin_config        chat_sessions
├── id (PK)         ├── id (PK)         ├── id (PK)
├── token (UQ)      ├── key (UQ)        ├── token (IDX)
├── name            └── value           ├── session_id (UQ)
├── created_at                          ├── session_number
└── expires_at                          └── created_at
```

### 6.2 前端类型定义

```typescript
// src/types/index.ts

// ── Token ──────────────────────────────────────
interface Token {
  id: number
  token: string
  name: string
  created_at: string    // ISO 8601
  expires_at: string    // ISO 8601
}

// ── Chat ───────────────────────────────────────
interface ChatSession {
  session_id: string
  session_number: number
  created_at: string
}

interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  timestamp: number
  tool_calls?: ToolCall[]
}

interface ToolCall {
  name: string
  arguments: Record<string, unknown>
  result?: string
  status: 'running' | 'completed' | 'error'
}

// ── SSE Events ─────────────────────────────────
type SSEEventType = 'text' | 'tool_call' | 'tool_result' | 'thinking' | 'error' | 'done'

interface SSEEvent {
  event: SSEEventType
  data: Record<string, unknown>
}

// ── API Response ───────────────────────────────
interface ApiResponse<T = unknown> {
  code: number
  error?: string
  [key: string]: T | number | string | undefined
}

// ── Admin ──────────────────────────────────────
interface TokenListResponse {
  code: number
  tokens: Token[]
  total: number
  page: number
  page_size: number
}
```

---

## 七、关键设计决策

### 7.1 SSE 流式处理

| 决策点 | 选择 | 理由 |
|---|---|---|
| SSE 客户端 | 原生 `fetch` + `ReadableStream` | 需要 `POST` 请求发起 SSE（`EventSource` 仅支持 GET）；当前实现已使用此方式 |
| 流解析 | 自定义 `useSSE` composable | 封装 SSE 事件解析、自动重连、错误处理 |

### 7.2 认证策略

| 场景 | 方案 |
|---|---|
| Chat/Bridge | Axios 拦截器自动注入 `Authorization: Bearer sk-xxx`，Token 存 `localStorage` |
| Admin | 后端通过 `Set-Cookie` 下发 `admin_session`，浏览器自动携带（需 CORS `credentials: true`） |
| 路由守卫 | `/admin` 路由进入前检查 Admin Session 有效性，无效则跳转登录 |

### 7.3 主题系统

| 决策 | 说明 |
|---|---|
| CSS 变量统一 | 将 3 个 HTML 文件中重复的 CSS 变量提取为 `variables.css`，通过 `data-theme` 属性切换 |
| Naive UI 主题 | 使用 Naive UI 的 `darkTheme` / `lightTheme`，与自定义 CSS 变量联动 |
| 持久化 | 主题偏好存入 `localStorage` |

### 7.4 需要移除的后端代码

| 文件 | 移除内容 |
|---|---|
| `server/routers/tokens.py` | `GET /` (serve_index)、`GET /admin` (serve_admin) |
| `server/routers/metrics.py` | `GET /metrics` (serve_metrics_dashboard) |
| `server/app.py` | `state.frontend_dir` 解析逻辑、`/static` mount |
| `Dockerfile` | `COPY frontend/ /app/frontend/` |

### 7.5 向后兼容

在过渡期间，可保留后端的 HTML 服务路由，使两种部署模式并存：
- **分离模式**：前端通过 Nginx 独立服务，后端不提供 HTML
- **一体模式**（向后兼容）：后端仍可服务前端构建产物（`dist/`）

通过环境变量 `SERVE_FRONTEND=true/false` 控制。

---

## 八、实现计划

### Phase 1：前端工程初始化（基础骨架）

1. 在项目根目录创建 `web/` 目录
2. 初始化 Vite + Vue 3 + TypeScript 工程
3. 安装依赖：`vue-router`、`pinia`、`naive-ui`、`axios`、`markdown-it`、`highlight.js`
4. 配置 `vite.config.ts`（proxy 到 `localhost:8765`）
5. 提取公共 CSS 变量到 `styles/variables.css`
6. 搭建 `App.vue` + 路由 + 基础布局

### Phase 2：API 层与状态管理

1. 封装 Axios 实例（`api/client.ts`）：baseURL、Token 拦截器、错误统一处理
2. 实现各 API 模块（`token.ts`、`admin.ts`、`chat.ts`、`media.ts`、`metrics.ts`）
3. 实现 Pinia Stores（`auth.ts`、`chat.ts`、`admin.ts`）
4. 实现 `useSSE` composable（SSE 流式消息处理）

### Phase 3：页面迁移

1. **ChatView** — 迁移聊天页（最复杂，优先）
   - 登录/Token 验证表单
   - 会话侧栏（创建/切换/管理会话）
   - 消息列表（Markdown 渲染、代码高亮、Tool Call 卡片）
   - 输入框（发送消息、文件拖拽上传）
   - SSE 流式响应
2. **AdminView** — 迁移管理页
   - Admin 登录/设置密码
   - Token 表格（分页、搜索、排序、筛选）
   - Token CRUD 弹窗
3. **MetricsView** — 迁移指标页

### Phase 4：后端改造

1. 添加 `CORSMiddleware`，origins 从环境变量读取
2. 添加 `SERVE_FRONTEND` 开关，条件注册 HTML 路由
3. 移除/条件化 `frontend_dir` 逻辑和 `/static` mount

### Phase 5：一键启动与部署

1. 项目根目录新增 `docker-compose.yml`（仅应用服务：前端 Nginx + 后端 FastAPI）
2. 新增前端 `Dockerfile`（多阶段构建：pnpm build → Nginx 服务）
3. 更新后端 `Dockerfile`（移除 frontend 拷贝）
4. 新增根目录 `Makefile`，提供开发/生产一键命令
5. 更新 CI/CD（前后端分别构建、分别部署）

---

## 九、一键启动设计

### 9.1 启动方式总览

| 场景 | 命令 | 说明 |
|---|---|---|
| **开发模式** | `make dev` | 同时启动 Vite dev server (HMR) + Uvicorn 后端 |
| **生产模式** | `docker compose up -d` | 启动前端 Nginx 容器 + 后端 FastAPI 容器 |
| **仅前端开发** | `make dev-web` | 只启动 Vite dev server |
| **仅后端开发** | `make dev-server` | 只启动 Uvicorn 后端 |
| **构建前端** | `make build-web` | 构建前端生产产物到 `web/dist/` |
| **停止生产** | `docker compose down` | 停止所有容器 |

### 9.2 Makefile（项目根目录）

```makefile
# Makefile — Astron Claw 一键启动

.PHONY: dev dev-web dev-server build-web clean

# ── 开发模式：前后端同时启动 ──────────────────────────
dev:
	@echo "Starting Astron Claw (dev mode)..."
	@trap 'kill 0' EXIT; \
	cd server && uv run python3 run.py & \
	cd web && pnpm dev & \
	wait

# ── 仅启动前端 dev server ─────────────────────────────
dev-web:
	cd web && pnpm dev

# ── 仅启动后端 ─────────────────────────────────────────
dev-server:
	cd server && uv run python3 run.py

# ── 构建前端生产产物 ───────────────────────────────────
build-web:
	cd web && pnpm install && pnpm build

# ── 安装所有依赖 ───────────────────────────────────────
install:
	cd server && uv sync
	cd web && pnpm install

# ── 清理构建产物 ───────────────────────────────────────
clean:
	rm -rf web/dist web/node_modules/.vite
```

**开发模式工作原理：**
- `make dev` 使用 `trap 'kill 0' EXIT` 实现进程组管理——`Ctrl+C` 一次即可同时终止前后端
- Vite dev server 监听 `:5173`，通过 proxy 转发 API 请求到后端 `:8765`
- 前端支持 HMR 热更新，后端日志实时输出

### 9.3 docker-compose.yml（生产部署）

仅编排前后端应用服务，MySQL / Redis / MinIO 使用外部已有实例。

```yaml
# docker-compose.yml — 仅应用服务，基础设施复用外部已有实例

services:
  # ── 后端 API 服务 ───────────────────────────────────
  backend:
    build:
      context: .
      dockerfile: server/Dockerfile
    container_name: astron-claw-backend
    restart: unless-stopped
    ports:
      - "${SERVER_PORT:-8765}:8765"
    env_file:
      - server/.env
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health')"]
      interval: 30s
      timeout: 5s
      start_period: 10s
      retries: 3

  # ── 前端 Nginx 服务 ─────────────────────────────────
  frontend:
    build:
      context: .
      dockerfile: web/Dockerfile
    container_name: astron-claw-frontend
    restart: unless-stopped
    ports:
      - "${WEB_PORT:-80}:80"
    environment:
      - BACKEND_URL=http://backend:8765
    depends_on:
      backend:
        condition: service_healthy
```

### 9.4 前端 Dockerfile（web/Dockerfile）

```dockerfile
# web/Dockerfile — 多阶段构建：pnpm build → Nginx 服务

# ── 构建阶段 ──────────────────────────────────────────
FROM node:20-alpine AS builder

WORKDIR /app

# 启用 pnpm
RUN corepack enable && corepack prepare pnpm@latest --activate

# 安装依赖（利用缓存层）
COPY web/package.json web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

# 构建生产产物
COPY web/ ./
RUN pnpm build

# ── 运行阶段 ──────────────────────────────────────────
FROM nginx:alpine

# 拷贝构建产物
COPY --from=builder /app/dist /usr/share/nginx/html

# 拷贝 Nginx 配置
COPY web/nginx.conf /etc/nginx/templates/default.conf.template

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
```

### 9.5 更新后端 Dockerfile（server/Dockerfile）

后端 Dockerfile 从项目根目录移至 `server/Dockerfile`，不再拷贝 `frontend/` 目录：

```dockerfile
# server/Dockerfile
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# ── 依赖层 ────────────────────────────────────────────
FROM base AS deps

WORKDIR /app/server

RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple/ uv

COPY server/pyproject.toml server/uv.lock ./
RUN uv sync --no-dev -i https://pypi.tuna.tsinghua.edu.cn/simple/

# ── 运行层 ────────────────────────────────────────────
FROM base

COPY --from=deps /app/server/.venv /app/server/.venv
COPY server/ /app/server/

WORKDIR /app/server

ENV PATH="/app/server/.venv/bin:$PATH"

RUN mkdir -p logs media

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health')" || exit 1

CMD ["python", "run.py"]
```

### 9.6 Nginx 配置（web/nginx.conf）

使用 envsubst 模板，`BACKEND_URL` 从环境变量注入：

```nginx
# web/nginx.conf — Nginx 配置模板
# 变量 ${BACKEND_URL} 由 docker 环境变量注入

server {
    listen 80;
    server_name _;

    root /usr/share/nginx/html;
    index index.html;

    # ── 前端 SPA fallback ─────────────────────────────
    location / {
        try_files $uri $uri/ /index.html;
    }

    # ── API 反向代理 ──────────────────────────────────
    location /api/ {
        proxy_pass ${BACKEND_URL};
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # ── SSE 反向代理（关闭缓冲）──────────────────────
    location /bridge/chat {
        proxy_pass ${BACKEND_URL};
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_http_version 1.1;
        proxy_set_header Connection '';
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 300s;
    }

    # ── WebSocket 反向代理 ────────────────────────────
    location /bridge/bot {
        proxy_pass ${BACKEND_URL};
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 86400s;
    }
}
```

### 9.7 Vite 配置（开发模式 proxy）

```typescript
// web/vite.config.ts
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8765',
        changeOrigin: true,
      },
      '/bridge': {
        target: 'http://localhost:8765',
        changeOrigin: true,
        ws: true,
      },
    },
  },
})
```

### 9.8 启动流程图

```
开发模式（make dev）:
┌────────────────────────────────────────────────────┐
│  Terminal                                          │
│  $ make dev                                        │
│    ├── server (Uvicorn :8765)  ─┐                  │
│    └── web (Vite :5173)  ──────┤                   │
│                                 │  Ctrl+C 同时终止  │
│  浏览器访问 http://localhost:5173                   │
│    /api/* ──proxy──→ localhost:8765                 │
│    /bridge/* ──proxy──→ localhost:8765              │
└────────────────────────────────────────────────────┘

生产模式（docker compose up -d）:
┌────────────────────────────────────────────────────┐
│  $ docker compose up -d                            │
│                                                    │
│  ┌──────────────────┐    ┌──────────────────────┐  │
│  │ frontend (:80)   │    │ backend (:8765)      │  │
│  │ Nginx + dist/    │───→│ FastAPI + Uvicorn    │  │
│  │ SPA fallback     │    │ REST + SSE + WS      │  │
│  └──────────────────┘    └──────────────────────┘  │
│                                                    │
│  浏览器访问 http://localhost                        │
│    /api/* ──nginx──→ backend:8765                  │
│    /bridge/* ──nginx──→ backend:8765               │
└────────────────────────────────────────────────────┘
```

---

## 十、文件变更清单

### 10.1 新增文件

| 文件 | 说明 |
|---|---|
| `Makefile` | 一键启动命令 |
| `docker-compose.yml` | 生产编排（仅应用服务） |
| `web/` | 前端 Vue 3 工程目录（完整） |
| `web/Dockerfile` | 前端多阶段构建镜像 |
| `web/nginx.conf` | Nginx 配置模板 |
| `server/Dockerfile` | 后端 Dockerfile（从根目录迁入） |

### 10.2 移除/修改文件

| 文件 | 变更 |
|---|---|
| `Dockerfile`（根目录） | 移除，拆分为 `server/Dockerfile` + `web/Dockerfile` |
| `frontend/` | 改造完成后可归档或删除 |
| `server/app.py` | 移除 `frontend_dir` 解析和 `/static` mount |
| `server/routers/tokens.py` | 移除 `GET /`、`GET /admin` 路由 |
| `server/routers/metrics.py` | 移除 `GET /metrics` HTML 路由 |

---

## 总结

| 项目 | 说明 |
|---|---|
| **改造风险** | **低** — 后端已是纯 API 架构，无服务端模板渲染 |
| **后端变更量** | **极小** — 仅新增 CORS + 移除 HTML 服务路由 |
| **前端工作量** | **主要工作** — 将 5400+ 行内联 HTML/CSS/JS 重构为 Vue 3 组件化 SPA |
| **一键启动** | `make dev` 开发模式 / `docker compose up -d` 生产模式 |
| **基础设施** | 复用外部已有的 MySQL、Redis、MinIO，compose 仅编排应用服务 |
| **兼容性** | 通过环境变量开关支持一体/分离两种部署模式平滑过渡 |
| **建议实施顺序** | Phase 1 → 2 → 3 → 4 → 5，可在 Phase 3 完成后即可交付使用 |
