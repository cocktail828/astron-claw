# 技术方案：done 事件内容渲染

## 1. 需求分析

### 1.1 问题

Bot 端对 `/models` 等命令的响应，直接作为 `agent_message_final` 一次性返回（无前置 `agent_message_chunk` 流式）。
服务端翻译后的 SSE 事件流为：

```
event: session
data: {"sessionId": "...", "sessionNumber": 108}

event: done
data: {"content": "Providers:\n- amazon-bedrock (83)\n..."}
```

正常对话流式场景：

```
event: session → event: chunk (N次) → event: done
```

前端通常在 `chunk` 事件中拼接渲染内容，`done` 仅作结束信号。
当 `done` 事件携带内容但无前置 `chunk` 时，内容丢失不被渲染。

### 1.2 目标

确保 `done` 事件中的 `content` 能被前端正确渲染，无论是否有前置 `chunk`。

---

## 2. 方案分析

### 方案 A：服务端拆分 — done 含内容时自动补发 chunk（推荐）

在 `_translate_bot_event()` 或 SSE 发送层，当 `done` 事件携带非空 `content` 时，
自动在 `done` 之前插入一个 `chunk` 事件承载内容，`done` 保留原始 `content` 不变。

**变更位置**：`routers/sse.py` 的 `_stream_response()` 中事件发送逻辑

**优点**：
- 前端零改动，`chunk` 拼接逻辑天然兼容
- 对所有客户端（Web、SDK、第三方）透明生效
- 保持 `done` 作为纯终止信号的语义一致性

**缺点**：
- 多发一个事件（微不足道的开销）

### 方案 B：前端适配 — done 事件也渲染 content

前端在处理 `done` 事件时，检查 `content` 是否非空，非空则渲染。

**优点**：服务端零改动

**缺点**：
- 需要所有客户端都适配（Web、SDK 等）
- `done` 语义不纯粹（既是终止信号又是内容载体）
- 正常流式场景 `done.content` 可能与最后一个 `chunk` 重复

---

## 3. 技术方案（方案 A）

### 3.1 变更范围

| 模块 | 文件 | 变更 |
|------|------|------|
| M1 | `routers/sse.py` | `_stream_response()` 中 `done` 事件拆分逻辑 |
| M2 | `tests/test_sse.py` | 新增测试：done 携带 content 时自动补发 chunk |

### 3.2 实现细节

在 `_stream_response()` 中增加 `has_chunks` 标记，追踪是否已有 `chunk` 事件发送。
仅当 `done` 携带非空 `content` **且无前置 `chunk`** 时，才补发 `chunk` 事件：

```python
has_chunks = False  # 在循环开始前初始化

# 事件分发处
if event_type == "chunk":
    has_chunks = True

# 终止事件处理
if event_type in ("done", "error"):
    # done 携带内容且无前置 chunk 时，先作为 chunk 发送，确保前端能渲染
    if event_type == "done" and not has_chunks and event_data.get("content"):
        yield _sse_event("chunk", {"content": event_data["content"]})
    yield _sse_event(event_type, event_data)
    return
```

### 3.3 事件流对比

**改造前（命令场景 — 无前置 chunk）**：
```
event: session  → event: done (content="Providers:\n...")
```

**改造后（命令场景 — 无前置 chunk）**：
```
event: session  → event: chunk (content="Providers:\n...") → event: done (content="Providers:\n...")
```

**正常流式场景（有前置 chunk — 无变化）**：
```
event: session  → event: chunk (N次) → event: done (content="全文")
```
正常流式场景已有 `chunk` 事件（`has_chunks=True`），`done.content` 不会被重复补发，
避免前端拼接出双倍内容。

---

## 4. 关键设计决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 变更层级 | SSE 发送层（sse.py） | 对 Bot 行为透明，无需改 bridge 翻译逻辑 |
| 拆分策略 | done → chunk + done | 保持 done 为纯终止信号，前端零改动 |
| done.content | 保留不清空 | 语义完整，第三方客户端和日志可利用 |
| error 事件 | 不拆分 | error 的 content 是错误描述，非正文内容 |

---

确认方案后可使用「代码实现」进入下一阶段。
