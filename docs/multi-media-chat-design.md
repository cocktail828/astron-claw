# 多媒体文件会话聊天 技术方案

## 1. 需求回顾

### 1.1 当前现状

`POST /bridge/chat` 接口的 `media` 字段为 `Optional[dict]`，只能携带**单个**媒体文件，且 `fileName`/`mimeType`/`fileSize` 为冗余参数：

```json
{
  "content": "描述这张图",
  "msgType": "image",
  "media": {
    "fileName": "photo.jpg",
    "mimeType": "image/jpeg",
    "fileSize": 102400,
    "downloadUrl": "http://host:9000/astron-claw-media/sid/photo.jpg"
  }
}
```

### 1.2 目标

| # | 需求 | 说明 |
|---|------|------|
| 1 | 多文件支持 | 单次对话可携带 N 个媒体文件 |
| 2 | 精简参数 | 移除 `fileName`/`mimeType`/`fileSize`/`msgType`，均可从 URL 推导 |
| 3 | 可扩展 | `media` 采用 `array[object]` 结构，通过 `type` 字段区分来源类型，便于日后扩展 |
| 4 | 不兼容旧版 | 彻底移除旧的 `msgType` + `media: dict` 格式，不做兼容 |

### 1.3 约束

- 插件侧 `inbound.ts` 已支持多 mediaItems 遍历，**无需改动插件代码**
- 变更仅涉及 Server 端

---

## 2. 接口设计

### 2.1 新版请求格式

```json
{
  "content": "帮我对比这两张图",
  "sessionId": "550e8400-...",
  "media": [
    { "type": "url", "url": "http://host:9000/astron-claw-media/sid/photo1.jpg" },
    { "type": "url", "url": "http://host:9000/astron-claw-media/sid/photo2.png" }
  ]
}
```

**纯文本消息：**

```json
{
  "content": "你好",
  "sessionId": "550e8400-..."
}
```

**纯媒体消息（无文本描述）：**

```json
{
  "media": [
    { "type": "url", "url": "http://host:9000/astron-claw-media/sid/voice.mp3" }
  ]
}
```

### 2.2 media 对象结构

通过 `type` 字段区分媒体来源，不同 type 有不同的必填字段：

| type | 必填字段 | 说明 | Server 归一化 |
|------|---------|------|--------------|
| `url` | `url` | HTTP(S) 下载地址 | 直接透传为 `downloadUrl` |
| `base64` | `data`, `mimeType` | Base64 编码的文件内容 | 解码 → 上传 S3 → 生成 `downloadUrl` |
| `s3_key` | `key` | S3 对象路径 | 生成签名 URL → `downloadUrl` |

> 当前只实现 `url`，`base64` / `s3_key` 为预留设计。

**type="url" 示例：**

```json
{ "type": "url", "url": "http://host:9000/astron-claw-media/sid/photo.jpg" }
```

**type="base64" 示例（预留）：**

```json
{ "type": "base64", "data": "iVBORw0KGgo...", "mimeType": "image/png" }
```

**type="s3_key" 示例（预留）：**

```json
{ "type": "s3_key", "key": "session-id/file.jpg" }
```

**归一化后的 JSON-RPC 内容（所有 type 统一）：**

```json
{
  "type": "media",
  "msgType": "file",
  "media": { "downloadUrl": "http://host:9000/astron-claw-media/..." }
}
```

> 插件只看到 `downloadUrl`，不感知原始 type。新增 type 只改 Server 归一化层，插件零改动。

### 2.3 字段变更对照

| 字段 | 旧版 | 新版 | 说明 |
|------|------|------|------|
| `msgType` | `"text"/"image"/"file"/"audio"/"video"` | **删除** | 有 `media` 即为媒体消息 |
| `media` | `Optional[dict]` | `Optional[list[MediaItem]]` | 从单对象变为数组 |
| `content` | 文本内容 | 不变 | 可与 media 共存 |
| `sessionId` | 可选 | 不变 | |

### 2.4 请求约束

| 约束 | 值 | 说明 |
|------|-----|------|
| `media` 最大长度 | 10 | 防止单次传入过多文件 |
| `media[].type` | 仅 `"url"` | 当前只支持 URL 类型 |
| `media[].url` 协议 | `http://` 或 `https://` | 安全校验 |
| `content` 与 `media` 都为空 | 400 错误 | 至少有一项 |

---

## 3. 架构设计

### 3.1 Server 归一化架构

核心设计原则：**无论客户端传入何种 `type`，Server 统一归一化为 `downloadUrl` 后再转发给插件。**

插件侧永远只看到 `downloadUrl`，不感知媒体来源类型。这使得新增 type 只需改 Server，插件零改动。

```
Client 请求                     Server 归一化层                  JSON-RPC → Plugin
─────────────                   ──────────────                   ──────────────
type: "url"    ── 直接透传 ──►  downloadUrl: "http://..."  ──►  下载 → 识别
type: "base64" ── 存S3    ──►  downloadUrl: "http://..."  ──►  下载 → 识别
type: "s3_key" ── 生成URL ──►  downloadUrl: "http://..."  ──►  下载 → 识别
```

### 3.2 数据流（仅 Server 端 ① ② 变更，插件侧不变）

```
                    ① 变更              ② 变更              ③ 不变
Chat Client ──── ChatRequest ──── send_to_bot() ──── Redis Stream ──── Plugin
             media: [{...}]     归一化 + 构建          JSON-RPC 转发    inbound.ts
                                content_items                        (已支持多 media)
```

### 3.3 影响范围

| 层 | 文件 | 变更 |
|----|------|------|
| 请求模型 | `server/routers/sse.py` | `ChatRequest` + `MediaItem` 模型重写 |
| 路由校验 | `server/routers/sse.py` | `chat_sse()` 校验逻辑简化 |
| 业务层 | `server/services/bridge.py` | `send_to_bot()` 签名和 content_items 构建 |
| 测试 | `server/tests/test_bridge.py`, `test_sse.py` | 更新用例 |
| 文档 | `docs/api.md` | 接口文档更新 |
| 插件 | `plugin/src/messaging/inbound.ts` | 清理对已移除字段的无效引用 |

---

## 4. 模块设计

### 4.1 请求模型（`server/routers/sse.py`）

```python
from pydantic import BaseModel, field_validator

class MediaItem(BaseModel):
    type: str           # "url" | "base64" | "s3_key"（当前只实现 url）
    url: str = ""       # type="url"
    data: str = ""      # type="base64"
    mimeType: str = ""  # type="base64"
    key: str = ""       # type="s3_key"

class ChatRequest(BaseModel):
    content: str = ""
    sessionId: Optional[str] = None
    media: Optional[list[MediaItem]] = None

    @field_validator("media")
    @classmethod
    def validate_media(cls, v):
        if v is not None and len(v) > 10:
            raise ValueError("Too many media items (max 10)")
        return v
```

### 4.2 chat_sse() 归一化 + 校验

将不同 type 的 media 统一解析为 `media_urls: list[str]`，再传给 `send_to_bot()`：

```python
content = body.content or ""
media_urls: list[str] = []

if body.media:
    for item in body.media:
        if item.type == "url":
            if not item.url.startswith(("http://", "https://")):
                return 400, f"Invalid media URL scheme: {item.url}"
            media_urls.append(item.url)
        # elif item.type == "base64":
        #     url = await media_manager.store_bytes(
        #         base64.b64decode(item.data), item.mimeType, session_id,
        #     )
        #     media_urls.append(url)
        # elif item.type == "s3_key":
        #     url = storage.presign_url(item.key)
        #     media_urls.append(url)
        else:
            return 400, f"Unsupported media type: {item.type}"

if not content and not media_urls:
    return 400, "Empty message"
```

> 注释部分为 base64/s3_key 的预留实现路径，当前不启用。

### 4.3 send_to_bot() 重构

**签名：**

```python
async def send_to_bot(
    self,
    token: str,
    user_message: str,
    media_urls: list[str] | None = None,
    session_id: str = "",
) -> Optional[str]:
```

**content_items 构建：**

```python
content_items = []

if user_message:
    content_items.append({"type": "text", "text": user_message})

for url in (media_urls or []):
    encoded_url = _ensure_encoded_url(url)
    content_items.append({
        "type": "media",
        "msgType": "file",
        "media": {"downloadUrl": encoded_url},
    })
```

- `msgType` 统一传 `"file"`，插件根据下载后的实际 MIME 推断（`inferMediaType`）
- `media` 字典只保留 `downloadUrl`

### 4.4 _ensure_encoded_url()

从现有 send_to_bot 中抽取 URL 编码逻辑：

```python
def _ensure_encoded_url(url: str) -> str:
    """Ensure URL path is properly percent-encoded (handles Unicode chars)."""
    from urllib.parse import urlparse, unquote, quote, urlunparse
    parsed = urlparse(url)
    decoded_path = unquote(parsed.path)
    encoded_path = quote(decoded_path, safe='/')
    return urlunparse((
        parsed.scheme, parsed.netloc, encoded_path,
        parsed.params, parsed.query, parsed.fragment
    ))
```

### 4.5 插件清理（`plugin/src/messaging/inbound.ts`）

移除对 Server 不再传递的 `mediaInfo.mimeType` / `mediaInfo.fileName` 的无效引用，改为从 URL 提取：

```typescript
// ---- 清理前 ----
// 主路径
contentType = loaded.contentType ?? mediaInfo.mimeType ?? "application/octet-stream";
fileName = loaded.fileName ?? mediaInfo.fileName ?? "file";
// 降级路径
contentType = resp.headers.get("content-type") ?? mediaInfo.mimeType ?? "application/octet-stream";
fileName = mediaInfo.fileName ?? "file";

// ---- 清理后 ----
// 从 URL path 提取文件名（不含扩展名），如 /sid/photo.jpg → "photo"
const urlBaseName = decodeURIComponent(
  new URL(downloadUrl).pathname.split("/").pop() ?? "file"
).replace(/\.[^.]+$/, "") || "file";

// 主路径
contentType = loaded.contentType ?? "application/octet-stream";
fileName = loaded.fileName ?? urlBaseName;
// 降级路径
contentType = resp.headers.get("content-type") ?? "application/octet-stream";
fileName = urlBaseName;
```

> `urlBaseName` 提取逻辑放在 try 块开头，两条路径共用。
> 效果：`photo---a1b2c3d4.jpg` 而非 `file---a1b2c3d4.jpg`。

---

## 5. 关键设计决策

### 5.1 为什么彻底移除 msgType？

插件 `inbound.ts:136` 的 `inferMediaType(contentType)` 已根据实际 MIME 做分类，不依赖 Server 传入的 `msgType`。调用方无需也不应该指定文件类型。

### 5.2 为什么 fileName/mimeType/fileSize 可以移除？

| 字段 | 插件使用情况 | 替代方案 |
|------|-------------|---------|
| `fileName` | fallback 文件名 | URL path 提取 or fallback `"file"` |
| `mimeType` | fallback Content-Type | HTTP 响应头 Content-Type |
| `fileSize` | **未使用** | — |

### 5.3 为什么 media 用 array[object] 而非 array[string]？

- **可扩展**：通过 `type` 字段分发，新增来源类型只需扩展 Server 归一化层
- **结构化**：显式 `type` 比约定"字符串一定是 URL"更健壮
- **可附加元数据**：不同 type 可携带各自需要的字段（url 只需 `url`，base64 需要 `data` + `mimeType`）

### 5.4 Server 归一化设计的优势

| 优势 | 说明 |
|------|------|
| 插件永远零改动 | 新增任何 media type 只改 Server，插件只认 `downloadUrl` |
| 避免大体积透传 | base64 在 Server 层转存 S3，不经过 Redis Stream / WebSocket |
| 关注点分离 | 存储逻辑（S3 上传/签名）收敛在 Server，插件只负责下载和推理 |
| 单一数据流 | JSON-RPC 中永远只有 `downloadUrl`，减少插件端分支逻辑 |

---

## 6. 实现计划

| 步骤 | 内容 | 文件 |
|------|------|------|
| 1 | 新增 `MediaItem` 模型，重写 `ChatRequest`（删除 `msgType`/旧 `media`） | `server/routers/sse.py` |
| 2 | 重写 `chat_sse()` 校验逻辑 | `server/routers/sse.py` |
| 3 | 重构 `send_to_bot()`：新签名 + 简化 content_items + 抽取 `_ensure_encoded_url` | `server/services/bridge.py` |
| 4 | 清理 `mediaInfo.mimeType` / `mediaInfo.fileName` 无效引用 | `plugin/src/messaging/inbound.ts` |
| 5 | 更新测试用例 | `server/tests/test_bridge.py`, `test_sse.py` |
| 6 | 更新 API 文档 | `docs/api.md` |
