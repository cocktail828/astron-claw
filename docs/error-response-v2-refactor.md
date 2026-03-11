# 技术方案：错误响应格式 V2 重构

## 1. 技术方案概述

### 1.1 需求

当前错误响应格式：
```json
{"ok": false, "error": "Password already set", "code": "ADMIN_PASSWORD_EXISTS"}
```

存在两个问题：
1. `code` 为字符串类型，期望改为**整型**，便于前端 switch/match 判断
2. `ok` 字段冗余 — 客户端可通过 HTTP 状态码或 `code` 判断成功/失败，无需额外字段

### 1.2 目标格式

统一通过 `code` 字段判断请求结果：`code = 0` 成功，`code > 0` 失败。

**成功响应**（HTTP 状态码 2xx）：
```json
{"code": 0, "token": "sk-abc123..."}
```

**错误响应**（HTTP 状态码 4xx/5xx）：
```json
{"code": 400, "error": "Password already set"}
```

---

## 2. 技术选型

### 2.1 整型错误码规则

`code` 直接复用 HTTP 状态码（整型），无需额外编码：

| code | 含义 |
|------|------|
| `0` | 成功 |
| `400` | 客户端请求错误 |
| `401` | 认证/授权失败 |
| `404` | 资源未找到 |
| `413` | 文件过大 |
| `500` | 服务端错误 |

### 2.2 完整错误码映射表

| 枚举成员 | code（整型） | message |
|---------|-------------|---------|
| AUTH_INVALID_TOKEN | `401` | Invalid or missing token |
| AUTH_MISSING_AUTH | `401` | Missing authorization |
| AUTH_INVALID_SESSION | `401` | Invalid admin session |
| AUTH_UNAUTHORIZED | `401` | Unauthorized |
| AUTH_WRONG_PASSWORD | `401` | Wrong password |
| ADMIN_PASSWORD_EXISTS | `400` | Password already set |
| ADMIN_PASSWORD_SHORT | `400` | Password too short |
| CHAT_EMPTY_MESSAGE | `400` | Empty message |
| CHAT_NO_BOT | `400` | No bot connected |
| CHAT_SEND_FAILED | `500` | Failed to send message to bot |
| CHAT_STREAM_TIMEOUT | — | Stream timeout |
| CHAT_INTERNAL_ERROR | — | Internal server error |
| MEDIA_FILE_TOO_LARGE | `413` | File too large |
| MEDIA_INVALID_FILE | `400` | Invalid file or unsupported type |
| MEDIA_BAD_URL_SCHEME | `400` | Invalid media URL scheme |
| MEDIA_UNSUPPORTED_TYPE | `400` | Unsupported media type |
| SESSION_NOT_FOUND | `404` | Session not found |
| TOKEN_NOT_FOUND | `404` | Token not found |
| WS_INVALID_TOKEN | `4001` | Invalid or missing bot token |
| WS_DUPLICATE_BOT | `4002` | Bot already connected |
| BOT_UNKNOWN_ERROR | — | Unknown error from bot |

---

## 3. 模块划分与实现计划

### M1: `infra/errors.py` — 响应函数重构

**变更点（仅 2 处）**：
1. `code` 属性返回 `self.status`（整型），原来返回 `self.name`（字符串）
2. `error_response()` 的 content 中 `"ok": False` → 移除，`code` 输出为整型

枚举成员定义完全不变，`(status, message)` 二元组不变。

```python
# code 属性变更
@property
def code(self) -> int:
    return self.status  # 直接复用 HTTP 状态码

# error_response 变更前
content={"ok": False, "error": message, "code": err.code}

# error_response 变更后
content={"code": err.code, "error": message}
```

### M2: 成功响应 `"ok": True` → `"code": 0`（8 处含 ok + 8 处不含 ok）

**原含 `"ok": True` 的路由（8 处）**：

| 文件 | 路由 | 当前返回 | 改为 |
|------|------|---------|------|
| `routers/sse.py` | `GET /bridge/chat/sessions` | `{"ok": True, "sessions": [...]}` | `{"code": 0, "sessions": [...]}` |
| `routers/sse.py` | `POST /bridge/chat/sessions` | `{"ok": True, "sessionId": ..., ...}` | `{"code": 0, "sessionId": ..., ...}` |
| `routers/metrics.py` | `DELETE /api/metrics` | `{"ok": True, "message": "..."}` | `{"code": 0, "message": "..."}` |
| `routers/admin.py` | `DELETE /api/admin/tokens/{token}` | `{"ok": True}` | `{"code": 0}` |
| `routers/admin.py` | `PATCH /api/admin/tokens/{token}` | `{"ok": True}` | `{"code": 0}` |
| `routers/admin_auth.py` | `POST /api/admin/auth/setup` | `{"ok": True}` | `{"code": 0}` |
| `routers/admin_auth.py` | `POST /api/admin/auth/login` | `{"ok": True}` | `{"code": 0}` |
| `routers/admin_auth.py` | `POST /api/admin/auth/logout` | `{"ok": True}` | `{"code": 0}` |

**原不含 `ok` 的路由（8 处）— 补充 `"code": 0`**：

| 文件 | 路由 | 当前返回 | 改为 |
|------|------|---------|------|
| `routers/admin_auth.py` | `GET /api/admin/auth/status` | `{"need_setup": ..., "authenticated": ...}` | `{"code": 0, "need_setup": ..., "authenticated": ...}` |
| `routers/admin.py` | `GET /api/admin/tokens` | `{"tokens": [...], "total": ..., ...}` | `{"code": 0, "tokens": [...], "total": ..., ...}` |
| `routers/admin.py` | `POST /api/admin/tokens` | `{"token": "..."}` | `{"code": 0, "token": "..."}` |
| `routers/admin.py` | `POST /api/admin/cleanup` | `{"removed_tokens": ..., ...}` | `{"code": 0, "removed_tokens": ..., ...}` |
| `routers/tokens.py` | `POST /api/token` | `{"token": "..."}` | `{"code": 0, "token": "..."}` |
| `routers/tokens.py` | `POST /api/token/validate` | `{"valid": ..., "bot_connected": ...}` | `{"code": 0, "valid": ..., "bot_connected": ...}` |
| `routers/health.py` | `GET /api/health` | `{"status": "ok", ...}` | `{"code": 0, "status": "ok", ...}` |
| `routers/media.py` | `POST /api/media/upload` | `{...media result...}` | `{"code": 0, ...media result...}` |

### M3: `docs/api.md` — 文档更新

- 更新统一错误响应格式定义（移除 `ok`，`code` 改为整型）
- 更新错误码清单表（新增 `code` 整型列）
- 更新各端点散布的错误/成功响应示例

### M4: 单元测试适配

- 更新 `tests/` 中所有涉及错误响应断言的测试用例

---

## 4. 关键设计决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 错误码类型 | 整型，复用 HTTP 状态码 | 无额外编码，直观、零学习成本 |
| 成功标识 | `code = 0` | 客户端统一判断 `code === 0` 即成功 |
| `ok` 字段 | 全局移除，用 `code` 替代 | 减少冗余，`code` 既可判断成功/失败又可区分错误类型 |
| 枚举定义 | 保持 `(status, message)` 不变 | `code` 直接取 `status`，无需改枚举结构 |
| 向后兼容 | 不保留 | 前端与客户端同步更新，一次性切换 |

---

## 5. 影响范围

| 层 | 文件数 | 改动类型 |
|----|--------|---------|
| 基础设施 | 1 (`infra/errors.py`) | 枚举定义 + 响应函数 |
| 路由 | 5 (`sse.py`, `admin.py`, `admin_auth.py`, `metrics.py`, `media.py`) | 移除成功响应 `ok` 字段 |
| 文档 | 1 (`docs/api.md`) | 格式定义 + 示例更新 |
| 测试 | 需排查 | 断言适配 |

---

确认方案后可使用「代码实现」进入下一阶段。
