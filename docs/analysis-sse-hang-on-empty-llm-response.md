# 分析报告：LLM 空响应导致 SSE 流挂起

## 1. 问题描述

**现象**：用户使用同一 session ID 进行多轮对话时，概率性出现 SSE 流卡住，客户端只收到心跳帧（`:heartbeat`），无法收到任何 AI 响应内容，最终在 10 分钟超时后才断开。

**触发条件**：LLM 最后一轮返回空 `content`（`content: []`），通常由模型调用报错引起。

## 2. 事件时序

以 session `f2c333f3` 的一次卡住为例，完整链路如下：

```
┌──────────────────────────────────────────────────────────────────────────┐
│ 1. 用户发送消息                                                         │
│    POST /bridge/chat → channel-backend (SSE endpoint)                   │
│    → purge(inbox) → ensure_group(inbox, "sse") → send_to_bot(token, msg)│
│    → 返回 StreamingResponse, SSE 消费端开始监听 Redis Stream            │
└──────────────────────────────┬───────────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ 2. Bot (OpenClaw Plugin) 收到 JSON-RPC session/prompt                   │
│    → SDK dispatchReplyWithBufferedBlockDispatcher 开始执行               │
│    → agent 运行 → tool call (edit) → tool result 成功                   │
│    → LLM 最终轮次请求模型 → 模型内部报错                                │
│    → SDK 吞掉错误, 返回 content:[], usage.totalTokens=0, isError=false  │
└──────────────────────────────┬───────────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ 3. Plugin 安全网判断 (inbound.ts:401-410)                                │
│                                                                          │
│    状态: chunkCount=0, lastPartialText="", allTurnsText=""               │
│                                                                          │
│    if (!finalSent && (chunkCount > 0 || lastPartialText || allTurnsText))│
│                       ^^^^^^^^^ false   ^^^^^^^^^^^^^^^ ""   ^^^^^^^^ "" │
│    → 条件为 false → sendFinal 不执行 → agent_message_final 未发送        │
└──────────────────────────────┬───────────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ 4. Plugin 发送 JSON-RPC result                                           │
│                                                                          │
│    hasResponse = finalSent || chunkCount > 0 → false                     │
│    → bridgeClient.send({ result: { stopReason: "no_reply" } })           │
└──────────────────────────────┬───────────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ 5. Channel Backend handle_bot_message (bridge.py:410-412)                │
│                                                                          │
│    if "id" in msg and "result" in msg:                                   │
│        logger.info("Bot result: req=... session=...")  ← 仅打日志        │
│        (不发送任何事件到 session inbox)                                   │
│                                                                          │
│    → done 事件从未写入 Redis Stream                                      │
└──────────────────────────────┬───────────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ 6. SSE 消费端 (_stream_response, sse.py:131-187)                         │
│                                                                          │
│    while time.time() < deadline:       # deadline = now + 600s           │
│        result = queue.consume(...)     # XREADGROUP BLOCK 5000ms         │
│        if result is None:                                                │
│            yield heartbeat             # 客户端只收到心跳                 │
│            continue                                                      │
│        if event_type in ("done", "error"):                               │
│            return                      # ← 永远走不到这里                │
│                                                                          │
│    → 10 分钟后超时, 发送 error 事件: "Chat stream timed out"              │
└──────────────────────────────────────────────────────────────────────────┘
```

## 3. 根因分析

### 3.1 直接原因

`inbound.ts:404` 的安全网条件过于严格：

```typescript
// 当前代码
if (!finalSent && (chunkCount > 0 || lastPartialText || allTurnsText))
```

该条件假设"只要 dispatch 产生过任何文本就需要发 final"，但遗漏了**模型零产出**的场景。当模型内部报错时，SDK 不调用 `onPartialReply`，不调用 `deliver`，直接 resolve dispatch，导致三个变量全为零/空值。

### 3.2 深层原因：SDK 错误吞没

OpenClaw SDK 内部捕获了模型调用错误，但**不以 exception 或 event 的形式暴露**：

| SDK 行为 | 值 | 说明 |
|---------|-----|------|
| `isError` | `false` | SDK 认为 run 正常完成 |
| `aborted` | `false` | 非中止 |
| `agent_error` event | 不触发 | SDK 未将模型错误归类为 agent error |
| 最终 assistant message | `content: [], usage.totalTokens: 0` | 唯一的错误指纹 |
| dispatch promise | 正常 resolve | 不 throw，插件无法 catch |

这意味着**插件层无法通过正常错误处理机制获知模型报错**，只能通过零产出的间接信号推断。

## 4. 跨产品对比：这不是 astron-claw 的架构问题

**adp-openclaw 和 ddingtalk 存在完全相同的逻辑缺陷**，只是传输协议的差异掩盖了问题的外在表现。

### 4.1 三方安全网代码对比

**astron-claw** (`plugin/src/messaging/inbound.ts:401-410`)：
```typescript
if (!finalSent && (chunkCount > 0 || lastPartialText || allTurnsText)) {
  const finalText = allTurnsText + lastPartialText;
  if (!isSilentReplyText(finalText, SILENT_REPLY_TOKEN)) {
    sendFinal(finalText);
  }
}
```
→ 零产出时不发 final。

**adp-openclaw** (dispatch 安全网)：
```typescript
if (!finalSent && chunkIndex > 0) {
  sendOutboundEnd(finalText);
}
```
→ 零产出时（`chunkIndex === 0`）**同样不发终止事件**。

**ddingtalk** (`src/monitor.ts:916-918`)：
```typescript
if (!queuedFinal) {
  logger.log(`no response generated for message from ${sender.label}`);
}
```
→ 零产出时**仅打日志**，不做任何补救。

### 4.2 为什么只有 astron-claw 卡住

| 插件 | 传输协议 | 零产出时行为 | 用户感知 |
|------|---------|------------|---------|
| **astron-claw** | SSE 长连接 | 不发 done → 流无终止信号 | **卡住 10 分钟**，UI 一直加载中 |
| **adp-openclaw** | WebSocket → Go server | 不发 done → Go 端有独立超时兜底 | 短暂等待后超时关闭，无明显卡顿 |
| **ddingtalk** | Webhook（HTTP 请求-响应） | 仅打日志 → 无长连接需要关闭 | 用户无回复，但无卡住感受 |

**结论**：三个产品共享同一套 SDK dispatch 逻辑，在模型零产出时都未发送终止事件。adp-openclaw 和 ddingtalk 因传输协议天然具备超时/无长连接兜底，问题被掩盖。astron-claw 采用 SSE 长连接，必须有显式终止事件才能关闭流，因此问题暴露为可感知的卡住。

**这不是 astron-claw 的代码架构问题，而是 SDK dispatch 安全网的通用缺陷在 SSE 传输协议下的特定表现。**

## 5. 影响范围

| 维度 | 影响 |
|------|------|
| **触发概率** | 取决于模型报错频率；用户报告为"概率性" |
| **影响时长** | 单次卡住 10 分钟（`_SSE_TIMEOUT = 600s`）后自动超时断开 |
| **影响范围** | 仅影响当次请求，不影响后续请求（session 不被永久破坏） |
| **客户端表现** | SSE 流只收到 `:heartbeat` 注释帧，无 `event: done`，UI 显示一直加载中 |

## 6. 修复方案

### 方案 A：Plugin 侧 — 无条件发送 sendFinal（推荐）

**修改文件**：`plugin/src/messaging/inbound.ts:401-410`

**修改逻辑**：dispatch resolve 即表示 agent 运行结束，无论有无文本产出，都必须发送 `agent_message_final` 以关闭 SSE 流。将条件从 `!finalSent && (chunkCount > 0 || ...)` 改为 `!finalSent`。

```typescript
if (!finalSent) {
  const finalText = allTurnsText + lastPartialText;
  if (chunkCount > 0 || finalText) {
    logger.info(`dispatch completed without final deliver, sending final (chunks=${chunkCount})`);
  } else {
    logger.warn("dispatch completed with zero output (model may have errored silently)");
  }
  sendFinal(isSilentReplyText(finalText, SILENT_REPLY_TOKEN) ? "" : finalText);
}
```

**优点**：从源头解决，保证 SSE 流收到终止事件。
**风险**：低。`sendFinal("")` 发送空文本 done 事件，SSE 端已支持空 content。

### 方案 B：Server 侧 — handle_bot_message 处理 no_reply result

**修改文件**：`server/services/bridge.py:410-412`

**修改逻辑**：收到 `result.stopReason == "no_reply"` 时，向 session 写入 done 事件。

```python
if "id" in msg and "result" in msg:
    session_id = msg.get("sessionId")
    stop_reason = msg.get("result", {}).get("stopReason", "")
    logger.info("Bot result: req={} stopReason={} session={} (token={}...)",
                msg["id"], stop_reason, session_id[:8] if session_id else "?", token[:10])
    if stop_reason == "no_reply" and session_id:
        await self._send_to_session(token, session_id, {"type": "done", "content": ""})
```

**优点**：Server 侧兜底，即使 Plugin 未修复也能工作。
**风险**：低。等同于发送空内容的 done 事件。

### 方案 C：两侧同时修复（最稳健）

方案 A + 方案 B 同时实施，形成双层保障。

## 7. 建议

1. **优先实施方案 A**（Plugin 侧），因为这是问题发生的源头层
2. **方案 B 作为防御性兜底**可选实施，增强 Server 层的鲁棒性
3. 后续可向 OpenClaw SDK 团队反馈：`dispatchReplyWithBufferedBlockDispatcher` 在模型报错时应通过 `agent_error` 事件或 reject promise 暴露错误信息，而非静默 resolve 空内容
