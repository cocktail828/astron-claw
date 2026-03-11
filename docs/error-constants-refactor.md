# 错误码与错误信息常量化技术方案

## 1. 技术方案概述

### 背景

Astron Claw 服务端当前有 **28 处**错误响应使用硬编码魔法值，分布在 **7 个文件**中。经全量审计发现：

- **4 处**相同消息 `"Invalid or missing token"` 分散在 3 个文件中
- **2 处**相同消息 `"Missing authorization"` 重复在同一文件中
- **2 种**不一致的响应体格式：`{"ok": False, "error": "..."}` vs `{"error": "..."}`
- WebSocket 关闭码（4001/4002）硬编码在路由中
- 无统一的错误码体系，客户端只能靠 HTTP 状态码 + 字符串匹配区分错误类型

### 目标

| 目标 | 衡量标准 |
|------|---------|
| 消灭魔法值 | 所有错误消息和状态码引用自常量定义 |
| 响应格式统一 | 全部 HTTP 错误响应使用统一的 `{"ok": false, "error": "...", "code": "..."}` 格式 |
| 可维护性提升 | 新增错误只需在一处定义，自动获得正确的 HTTP 状态码和消息 |
| 客户端可编程 | 客户端可通过 `code` 字段程序化区分错误类型 |

### 约束

- 不引入新依赖
- 不改变现有 HTTP 状态码语义（保持向后兼容）
- 使用 Python Enum，零运行时开销
- 最小侵入：不改变路由函数签名和请求处理逻辑

---

## 2. 技术选型

| 项目 | 选型 | 理由 |
|------|------|------|
| 常量定义 | `enum.Enum` | Python 标准库，类型安全，IDE 自动补全 |
| 响应构造 | 辅助函数 `error_response()` | 统一格式、减少样板代码、可扩展 |
| 文件位置 | `infra/errors.py` | 与 `infra/log.py`、`infra/config.py` 同层，属基础设施 |

---

## 3. 现状诊断

### 3.1 按文件分布

| 文件 | 错误响应数 | 格式 |
|------|-----------|------|
| `routers/sse.py` | 11 (HTTP) + 2 (SSE) + 2 (Pydantic) | `{"ok": false, "error": "..."}` |
| `routers/metrics.py` | 3 | `{"ok": false, "error": "..."}` |
| `routers/media.py` | 3 | `{"error": "..."}` |
| `routers/admin.py` | 2 | `{"error": "..."}` |
| `routers/admin_auth.py` | 3 | `{"error": "..."}` |
| `routers/websocket.py` | 2 (WS close) + 1 (WS JSON) | WebSocket |
| `services/bridge.py` | 1 (SSE 转发) | SSE event |

### 3.2 重复消息

| 消息 | 出现次数 | 文件 |
|------|---------|------|
| `"Invalid or missing token"` | 4 | sse.py ×3, media.py ×1 |
| `"Missing authorization"` | 2 | metrics.py ×2 |
| `"Unauthorized"` / `"Invalid admin session"` | 2 | admin.py ×1, metrics.py ×1 |

### 3.3 响应格式不一致

- **SSE / Metrics 路由**: `{"ok": False, "error": "..."}`
- **Admin / Media / Auth 路由**: `{"error": "..."}`（缺少 `ok` 字段）

---

## 4. 架构设计

### 4.1 新增文件

```
server/infra/errors.py    # 错误码枚举 + error_response 辅助函数
```

### 4.2 数据流

```
路由函数
  → 引用 Err.AUTH_INVALID_TOKEN
  → 调用 error_response(Err.AUTH_INVALID_TOKEN)
  → 返回 JSONResponse(status_code=401, content={"ok": false, "error": "...", "code": "AUTH_INVALID_TOKEN"})
```

---

## 5. 详细设计

### 5.1 错误码枚举定义 — `infra/errors.py`

```python
from enum import Enum
from fastapi.responses import JSONResponse


class Err(Enum):
    """Application error codes.

    Each member is a tuple of (http_status_code, error_message).
    """

    # ── Auth (token) ──────────────────────────────────────
    AUTH_INVALID_TOKEN      = (401, "Invalid or missing token")
    AUTH_MISSING_AUTH       = (401, "Missing authorization")
    AUTH_INVALID_SESSION    = (401, "Invalid admin session")
    AUTH_UNAUTHORIZED       = (401, "Unauthorized")
    AUTH_WRONG_PASSWORD     = (401, "Wrong password")

    # ── Admin setup ───────────────────────────────────────
    ADMIN_PASSWORD_EXISTS   = (400, "Password already set")
    ADMIN_PASSWORD_SHORT    = (400, "Password too short")

    # ── Chat / SSE ────────────────────────────────────────
    CHAT_EMPTY_MESSAGE      = (400, "Empty message")
    CHAT_NO_BOT             = (400, "No bot connected")
    CHAT_SEND_FAILED        = (500, "Failed to send message to bot")
    CHAT_STREAM_TIMEOUT     = (None, "Stream timeout")          # SSE event, no HTTP code
    CHAT_INTERNAL_ERROR     = (None, "Internal server error")   # SSE event, no HTTP code

    # ── Media ─────────────────────────────────────────────
    MEDIA_FILE_TOO_LARGE    = (413, "File too large")
    MEDIA_INVALID_FILE      = (400, "Invalid file or unsupported type")
    MEDIA_BAD_URL_SCHEME    = (400, "Invalid media URL scheme")
    MEDIA_UNSUPPORTED_TYPE  = (400, "Unsupported media type")

    # ── Session ───────────────────────────────────────────
    SESSION_NOT_FOUND       = (404, "Session not found")

    # ── Token (admin CRUD) ────────────────────────────────
    TOKEN_NOT_FOUND         = (404, "Token not found")

    # ── WebSocket ─────────────────────────────────────────
    WS_INVALID_TOKEN        = (4001, "Invalid or missing bot token")
    WS_DUPLICATE_BOT        = (4002, "Bot already connected")

    # ── Bot (internal) ────────────────────────────────────
    BOT_UNKNOWN_ERROR       = (None, "Unknown error from bot")

    def __init__(self, status: int | None, message: str):
        self.status = status
        self.message = message

    @property
    def code(self) -> str:
        """Return the enum member name as the error code string."""
        return self.name


def error_response(err: Err, detail: str = "") -> JSONResponse:
    """Build a unified JSON error response.

    Args:
        err: An ``Err`` enum member.
        detail: Optional extra detail appended to the message
                (e.g. the invalid value).

    Returns:
        ``JSONResponse`` with body ``{"ok": false, "error": "...", "code": "..."}``.
    """
    message = f"{err.message}: {detail}" if detail else err.message
    return JSONResponse(
        status_code=err.status,
        content={"ok": False, "error": message, "code": err.code},
    )
```

### 5.2 使用示例（改造前 vs 改造后）

**改造前** (`routers/sse.py`):
```python
return JSONResponse(
    status_code=401,
    content={"ok": False, "error": "Invalid or missing token"},
)
```

**改造后**:
```python
from infra.errors import Err, error_response

return error_response(Err.AUTH_INVALID_TOKEN)
```

**带动态详情** (`routers/sse.py`):
```python
# 改造前
return JSONResponse(
    status_code=400,
    content={"ok": False, "error": f"Unsupported media type: {item.type}"},
)

# 改造后
return error_response(Err.MEDIA_UNSUPPORTED_TYPE, detail=item.type)
```

**WebSocket 场景** (`routers/websocket.py`):
```python
# 改造前
await ws.close(code=4001, reason="Invalid or missing bot token")

# 改造后
await ws.close(code=Err.WS_INVALID_TOKEN.status, reason=Err.WS_INVALID_TOKEN.message)
```

**SSE 事件场景** (`routers/sse.py`):
```python
# 改造前
yield _sse_event("error", {"content": "Stream timeout"})

# 改造后
yield _sse_event("error", {"content": Err.CHAT_STREAM_TIMEOUT.message})
```

---

## 6. 响应体格式规范

统一所有 HTTP 错误响应为：

```json
{
  "ok": false,
  "error": "Human-readable error message",
  "code": "AUTH_INVALID_TOKEN"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `ok` | `boolean` | 始终为 `false` |
| `error` | `string` | 人类可读的错误描述（可含动态详情） |
| `code` | `string` | 程序化错误码，与枚举名一致，客户端可用于 switch/match |

---

## 7. 完整错误码清单

| 错误码 | HTTP | 消息 | 使用位置 |
|--------|------|------|---------|
| `AUTH_INVALID_TOKEN` | 401 | Invalid or missing token | sse.py ×3, media.py ×1 |
| `AUTH_MISSING_AUTH` | 401 | Missing authorization | metrics.py ×2 |
| `AUTH_INVALID_SESSION` | 401 | Invalid admin session | metrics.py ×1 |
| `AUTH_UNAUTHORIZED` | 401 | Unauthorized | admin.py ×1 |
| `AUTH_WRONG_PASSWORD` | 401 | Wrong password | admin_auth.py ×1 |
| `ADMIN_PASSWORD_EXISTS` | 400 | Password already set | admin_auth.py ×1 |
| `ADMIN_PASSWORD_SHORT` | 400 | Password too short | admin_auth.py ×1 |
| `CHAT_EMPTY_MESSAGE` | 400 | Empty message | sse.py ×1 |
| `CHAT_NO_BOT` | 400 | No bot connected | sse.py ×1 |
| `CHAT_SEND_FAILED` | 500 | Failed to send message to bot | sse.py ×1 |
| `CHAT_STREAM_TIMEOUT` | — | Stream timeout | sse.py SSE ×1 |
| `CHAT_INTERNAL_ERROR` | — | Internal server error | sse.py SSE ×1 |
| `MEDIA_FILE_TOO_LARGE` | 413 | File too large | media.py ×1 |
| `MEDIA_INVALID_FILE` | 400 | Invalid file or unsupported type | media.py ×1 |
| `MEDIA_BAD_URL_SCHEME` | 400 | Invalid media URL scheme | sse.py ×1 |
| `MEDIA_UNSUPPORTED_TYPE` | 400 | Unsupported media type | sse.py ×1 |
| `SESSION_NOT_FOUND` | 404 | Session not found | sse.py ×1 |
| `TOKEN_NOT_FOUND` | 404 | Token not found | admin.py ×1 |
| `WS_INVALID_TOKEN` | 4001 | Invalid or missing bot token | websocket.py ×1 |
| `WS_DUPLICATE_BOT` | 4002 | Bot already connected | websocket.py ×1 |
| `BOT_UNKNOWN_ERROR` | — | Unknown error from bot | bridge.py ×1 |

---

## 8. 模块划分

| 模块 | 文件范围 | 改动类型 |
|------|---------|---------|
| **M1: 错误码定义** | `infra/errors.py` (新建) | 新增枚举 + 辅助函数 |
| **M2: SSE 路由** | `routers/sse.py` | 替换 11 处 HTTP + 2 处 SSE |
| **M3: Metrics 路由** | `routers/metrics.py` | 替换 3 处 |
| **M4: Media 路由** | `routers/media.py` | 替换 3 处 |
| **M5: Admin 路由** | `routers/admin.py`, `routers/admin_auth.py` | 替换 5 处 |
| **M6: WebSocket 路由** | `routers/websocket.py` | 替换 2 处 WS close + 1 处 JSON |
| **M7: Bridge 服务** | `services/bridge.py` | 替换 1 处 |

---

## 9. 关键设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 常量机制 | `Enum` 而非 dict/module-level 常量 | 类型安全、IDE 补全、防拼写错误 |
| code 字段值 | 使用枚举名 (`AUTH_INVALID_TOKEN`) | 无需维护额外映射，自动与代码同步 |
| 动态消息 | `detail` 参数追加 | 兼顾常量化与动态上下文（如 `item.type`） |
| 响应体统一 | 所有路由统一为 `{"ok", "error", "code"}` | 解决现有 2 种格式不一致问题 |
| WebSocket 码 | 纳入同一枚举（`status` 为 4001/4002） | 统一管理，`error_response` 不用于 WS，仅引用 `.status` 和 `.message` |
| SSE 事件 | `status=None` 标记 | 这些不走 HTTP 响应，仅引用 `.message` |
| 文件大小消息 | 动态 detail | `MEDIA_FILE_TOO_LARGE` 消息为固定前缀，实际大小限制通过 detail 传入 |

---

## 10. 实现计划

| 顺序 | 模块 | 预估改动 | 说明 |
|------|------|---------|------|
| 1 | M1: 错误码定义 | 新建 `infra/errors.py` | 基础设施，其他模块依赖 |
| 2 | M2: SSE 路由 | `routers/sse.py` ~13 处 | 改动最多的文件 |
| 3 | M3: Metrics 路由 | `routers/metrics.py` ~3 处 | |
| 4 | M4: Media 路由 | `routers/media.py` ~3 处 | |
| 5 | M5: Admin 路由 | `admin.py` + `admin_auth.py` ~5 处 | |
| 6 | M6: WebSocket 路由 | `routers/websocket.py` ~3 处 | |
| 7 | M7: Bridge 服务 | `services/bridge.py` ~1 处 | |

---

## 11. 验证清单

- [ ] 所有路由文件中无硬编码状态码和错误消息字符串
- [ ] 全部 HTTP 错误响应包含 `ok`、`error`、`code` 三个字段
- [ ] WebSocket close 码和 reason 引用自 `Err` 枚举
- [ ] SSE 错误事件消息引用自 `Err` 枚举
- [ ] 现有测试全部通过（HTTP 状态码不变）
- [ ] `Err` 枚举成员数 = 21（覆盖全部错误场景）

---

确认后可使用「代码实现」进入下一阶段。
