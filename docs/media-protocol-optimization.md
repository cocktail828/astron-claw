# 多媒体消息协议优化 技术方案

## 1. 需求回顾

### 1.1 当前问题

系统中存在三层媒体协议，当前结构存在冗余、不统一和语义不清：

**问题一：Chat→Server（HTTP 请求）输入侧字段不统一**

```json
{ "type": "url", "url": "http://..." }
{ "type": "base64", "data": "iVBOR...", "mimeType": "image/png" }
{ "type": "s3_key", "key": "session-id/file.jpg" }
```

- 每种 type 各用一个字段名（`url`、`data`、`key`），不统一
- 主值字段名与 type 绑定，新增 type 就要新增字段

**问题二：Server→Bot（JSON-RPC content items）结构冗余**

```json
{
  "type": "media",
  "msgType": "file",
  "media": { "downloadUrl": "http://host:9000/astron-claw-media/sid/photo.jpg" }
}
```

- `type: "media"` 已表明是媒体 → `msgType: "file"` 多余
- `media: { downloadUrl }` 嵌套了一层对象，但只有一个字段，完全可以展平

**问题三：Bot→Chat（SSE 事件）结构冗余且语义模糊**

```json
{
  "type": "message",
  "msgType": "file",
  "content": "",
  "media": {
    "fileName": "photo.jpg",
    "mimeType": "image/jpeg",
    "fileSize": 102400,
    "downloadUrl": "http://host:9000/astron-claw-media/sid/photo.jpg"
  }
}
```

- `type: "message"` 过于笼统，无法区分文本消息和媒体消息
- `msgType` 与 `type` 职责模糊
- `media` 嵌套对象字段冗余

### 1.2 优化目标

| # | 目标 | 说明 |
|---|------|------|
| 1 | 全链路统一 `content` | 三层协议的主值字段统一为 `content`，一以贯之 |
| 2 | 消除嵌套 | 展平 `media: { downloadUrl }` 为 `content` 字段 |
| 3 | 统一二元结构 | 所有内容项采用 `type` + `content` 模式 |
| 4 | SSE 事件语义清晰 | `type: "media"` 替代 `type: "message"`，一眼可辨 |
| 5 | 删除冗余字段 | 移除 `msgType`、`fileSize` 等可推导/未使用字段 |

---

## 2. 核心设计原则

**全链路 `content` 一以贯之**：三层协议的主值字段统一为 `content`。

| 层 | 结构 | 主值字段 |
|---|------|---------|
| Chat→Server | `{type, content, mimeType?}` | `content` |
| Server→Bot | `{type, content}` | `content` |
| Bot→Chat | SSE `event: media`，`data: {type, content}` | `content` |

---

## 3. 三层数据流分析

| 方向 | 路径 | 协议角色 |
|------|------|----------|
| Chat→Server（输入） | HTTP `POST /bridge/chat` | 客户端请求，`type` 表示**媒体来源方式** |
| Server→Bot（入站） | `send_to_bot()` → Redis → Plugin `inbound.ts` | JSON-RPC content items，`type` 表示**内容编码方式** |
| Bot→Chat（出站） | Plugin → WS → `_translate_bot_event()` → SSE → Chat | SSE events，`type` 表示**事件类别** |

三个方向的 `type` 职责不同，结构应各自合理。

---

## 4. 协议优化设计

### 4.1 Chat→Server（HTTP 请求，优化后）

`type` 表示媒体来源方式，`content` 统一承载主值，辅助字段按需附加。

**Before：**

```json
{ "type": "url", "url": "http://..." }
{ "type": "base64", "data": "iVBOR...", "mimeType": "image/png" }
{ "type": "s3_key", "key": "session-id/file.jpg" }
```

**After：**

```json
{ "type": "url", "content": "http://..." }
{ "type": "base64", "content": "iVBOR...", "mimeType": "image/png" }
{ "type": "s3_key", "content": "session-id/file.jpg" }
```

| type | content 含义 | 辅助字段 |
|------|-------------|---------|
| `url` | HTTP(S) 下载地址 | 无 |
| `base64` | Base64 编码的文件内容 | `mimeType`（必填，无法从 base64 推断） |
| `s3_key` | S3 对象路径 | 无 |

变更点：

- `url`、`data`、`key` 三个字段统一收敛为 `content` — 消除每种 type 各用一个字段名的不一致
- `mimeType` 保留为 base64 的辅助字段 — 这是唯一无法从主值推断的信息

Pydantic 模型简化：

```python
class MediaItem(BaseModel):
    type: str            # "url" | "base64" | "s3_key"（当前只实现 url）
    content: str         # 主值：URL / base64 数据 / S3 key
    mimeType: str = ""   # 仅 base64 需要
```

### 4.2 Server→Bot（JSON-RPC content items）

`type` 表示内容编码方式，`content` 统一承载内容值。

**Before：**

```json
[
  { "type": "text", "text": "描述这张图" },
  { "type": "media", "msgType": "file", "media": { "downloadUrl": "http://..." } }
]
```

**After：**

```json
[
  { "type": "text", "content": "描述这张图" },
  { "type": "url", "content": "http://host:9000/astron-claw-media/sid/photo.jpg" }
]
```

| type | content 含义 | 说明 |
|------|-------------|------|
| `text` | 文本内容 | 原 `text` 字段统一为 `content` |
| `url` | 下载地址 | 消除 `media` 嵌套，Plugin 直接读 `content` 下载 |

变更点：

- 删除 `msgType` — Plugin 本就用 `inferMediaType(contentType)` 从下载内容推断
- 删除 `media: { downloadUrl }` 嵌套 — 展平为 `content`
- `text` 字段名统一为 `content` — 两种 type 结构完全一致

### 4.3 Bot→Chat（SSE 事件）

事件类别通过 SSE `event:` 字段标识（`event: media`），`data` 内只放 `type` + `content` 二元结构，与其他两层协议完全一致。

- `type` 表示内容格式（`url` / `base64`）
- `content` 承载对应的值

**Before：**

```json
{
  "type": "message",
  "msgType": "file",
  "content": "",
  "media": {
    "fileName": "photo.jpg",
    "mimeType": "image/jpeg",
    "fileSize": 102400,
    "downloadUrl": "http://host:9000/astron-claw-media/sid/photo.jpg"
  }
}
```

**After：**

```
event: media
data: {"type": "url", "content": "http://host:9000/astron-claw-media/sid/photo.jpg"}
```

将来如果支持 base64（如小缩略图内联）：

```
event: media
data: {"type": "base64", "content": "iVBORw0KGgo..."}
```

变更点：

- 事件类别从 JSON `type: "message"` 移至 SSE `event: media` — 语义明确且不占用 data 字段
- `data` 内只保留 `type`（内容格式）+ `content`（内容值）— 与 Chat→Server、Server→Bot 结构一致
- 删除 `msgType`、`fileSize`、`mimeType`、`media` 嵌套 — 全部冗余
- `fileName` 按需由前端从 URL 提取，不再由 Server 透传

### 4.4 SSE 事件类型总览（优化后）

| SSE event type | 含义 | 语义清晰度 |
|----------------|------|-----------|
| `session` | 会话信息 | 明确 |
| `chunk` | 文本流片段 | 明确 |
| `thinking` | 思考过程 | 明确 |
| `tool_call` | 工具调用 | 明确 |
| `tool_result` | 工具结果 | 明确 |
| **`media`** | **媒体文件** | **改后明确**（原 `message` 模糊） |
| `done` | 完成 | 明确 |
| `error` | 错误 | 明确 |

---

## 5. 完整接口对照

### 5.1 Chat→Server（HTTP 请求，优化后）

```json
{
  "content": "帮我对比这两张图",
  "sessionId": "550e8400-...",
  "media": [
    { "type": "url", "content": "http://host:9000/.../photo1.jpg" },
    { "type": "url", "content": "http://host:9000/.../photo2.png" }
  ]
}
```

### 5.2 Server→Bot（JSON-RPC content items，优化后）

```json
{
  "jsonrpc": "2.0",
  "method": "session/prompt",
  "params": {
    "content": [
      { "type": "text", "content": "帮我对比这两张图" },
      { "type": "url", "content": "http://host:9000/.../photo1.jpg" },
      { "type": "url", "content": "http://host:9000/.../photo2.png" }
    ]
  }
}
```

### 5.3 Bot→Chat（SSE event，优化后）

```
event: media
data: {"type":"url","content":"http://host:9000/.../photo.jpg"}
```

---

## 6. 新旧协议对比总结

### 6.1 Chat→Server MediaItem

| 维度 | 旧方案 | 新方案 |
|------|--------|--------|
| 主值字段 | 每种 type 各一个（`url`、`data`、`key`） | 统一 `content` |
| 字段数 | 2-3 个（因 type 而异） | 2 个（`type`, `content`），base64 额外 `mimeType` |
| 新增 type | 需定义新字段名 | 只需定义 `content` 语义 |
| Pydantic 模型 | 5 个字段（`type`, `url`, `data`, `mimeType`, `key`） | 3 个字段（`type`, `content`, `mimeType`） |

### 6.2 Server→Bot content item

| 维度 | 旧方案 | 新方案 |
|------|--------|--------|
| 嵌套层级 | 3 层 (`item.media.downloadUrl`) | 1 层 (`item.content`) |
| 字段数 | 3 个 (`type`, `msgType`, `media`) | 2 个 (`type`, `content`) |
| 结构统一性 | `type: "text"` 用 `text`，`type: "media"` 用 `media.downloadUrl` — 不统一 | 所有 type 都用 `content` — 统一 |
| Plugin 解析 | 需要 `item.media?.downloadUrl` 可选链 | 直接 `item.content` |

### 6.3 Bot→Chat SSE event

| 维度 | 旧方案 | 新方案 |
|------|--------|--------|
| 事件类型 | JSON `type: "message"` — 模糊 | SSE `event: media` — 明确 |
| data 结构 | 6 字段嵌套 (`type`, `msgType`, `content`, `media.fileName/mimeType/fileSize/downloadUrl`) | 2 字段 (`type`, `content`) |
| 嵌套层级 | 2 层 (`event.media.downloadUrl`) | 0 层 (`data.content`) |
| 与其他层一致性 | 独立结构，与 Chat→Server / Server→Bot 不同 | `{type, content}` 三层统一 |
| 可扩展性 | 新增来源需修改 `media` 嵌套结构 | 新增 `type` 值即可（如 `"base64"`） |

---

## 7. 关键设计决策

### 7.1 为什么全链路统一用 `content` 作为主值字段？

- **一致性**：三层协议结构统一，降低认知负担
- **可预测**：无论哪层，拿到一个 item 就知道主值在 `content` 里
- **简化模型**：输入侧 Pydantic 模型从 5 个字段缩减为 3 个

### 7.2 为什么 base64 的 `mimeType` 保留为独立字段而非合并到 `content`？

`mimeType` 是 base64 的辅助元数据，无法从 base64 编码内容推断。`content` 只承载主值（base64 数据本身），辅助信息作为额外字段附加。这保持了 `content` 语义的纯粹性。

### 7.3 为什么 JSON-RPC content items 用 `type: "url"` + `content`？

- **二元统一**：所有 content item 都是 `{type, content}`，解析逻辑零分支
- **语义直白**：`type` 直接告诉消费方内容的编码方式
- **展平结构**：消除无意义的 `media: { downloadUrl }` 嵌套

### 7.4 为什么 SSE 事件的 data 也用 `{type, content}` 而不是引入 `source` 字段？

事件类别已通过 SSE `event: media` 字段标识，`data` 内无需再放事件类型，`type` 字段自然释放给内容格式使用。这样三层协议的 `data` 全部是 `{type, content}`，零额外概念。

### 7.5 为什么 SSE 事件类型从 `message` 改为 `media`？

`message` 是所有事件类型中唯一需要"猜"的——消息可以是文本、通知、系统消息。改为 `media` 后，每个 SSE event type 都是具体名词，直接描述载荷内容，一眼可辨。

---

## 8. 影响范围

| 层 | 文件 | 变更内容 |
|----|------|---------|
| 请求模型 | `server/routers/sse.py` | `MediaItem` 模型：`url`/`data`/`key` 三字段合并为 `content` |
| 业务层 | `server/services/bridge.py` `send_to_bot()` | content_items 从 `{"type":"media","msgType":"file","media":{"downloadUrl":url}}` 改为 `{"type":"url","content":url}`；text item 的 `text` 改为 `content` |
| 业务层 | `server/services/bridge.py` `_translate_bot_event()` | `agent_media` 翻译结果：SSE event 改为 `event: media`，data 从嵌套结构改为 `{"type":"url","content":url}` |
| 插件 | `plugin/src/messaging/inbound.ts` | content item 解析：`type === "media"` → `type === "url"`，读 `item.content` 替代 `item.media.downloadUrl`；text item 读 `item.content` 替代 `item.text` |
| 前端 | Chat 前端 | SSE 事件监听：`type: "message"` → `event: media`；媒体数据读取：`event.media.downloadUrl` → `data.content`；移除 `msgType` 分支逻辑 |
| 测试 | `server/tests/test_bridge.py` | 更新 content_items 断言 |
| 测试 | `server/tests/test_sse.py` | 更新 MediaItem 输入侧断言 |
| 文档 | `docs/api.md` | 更新 JSON-RPC content items 格式、SSE media 事件格式 |

---

## 9. 实现计划

| 步骤 | 内容 | 文件 |
|------|------|------|
| 1 | `MediaItem` 模型重写：`url`/`data`/`key` 合并为 `content` | `server/routers/sse.py` |
| 2 | `chat_sse()` 校验逻辑适配新字段名 | `server/routers/sse.py` |
| 3 | 修改 `send_to_bot()` 的 content_items 构建逻辑 | `server/services/bridge.py` |
| 4 | 修改 `_translate_bot_event()` 的 `agent_media` 翻译逻辑 | `server/services/bridge.py` |
| 5 | 更新 Plugin `inbound.ts` 的 content item 解析 | `plugin/src/messaging/inbound.ts` |
| 6 | 前端 Chat 界面适配：SSE 媒体事件监听改为 `event: media`，数据解析改为 `{type, content}` | Chat 前端 |
| 7 | 更新测试用例 | `server/tests/test_bridge.py`, `test_bridge_translate.py`, `test_sse.py` |
| 8 | 更新 API 文档 | `docs/api.md` |
