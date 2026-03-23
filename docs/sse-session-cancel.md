# SSE 会话中断（Cancel）功能实现计划

## Context

当前 astron-claw 用户点击"停止"按钮时，前端仅断开 SSE 连接（`AbortController.abort()`），但 **Bot 端的 AI 生成不会被中止**——Bot 会继续运行直到自然结束，浪费 token 和算力。

目标：实现类似 openclaw web 的对话打断功能——用户点击停止后，Bot 端真正中止 LLM 调用，用户可以立即发送新消息，且历史对话（包括被打断的部分内容）完整保留。

### OpenClaw WebUI 的做法（参考）

OpenClaw web 使用 WebSocket 直连 Gateway，cancel 流程：
1. UI 点 Stop → 通过 WS 发 `chat.abort` RPC（`{sessionKey, runId}`）
2. Gateway `abortChatRunById()`（`src/gateway/chat-abort.ts`）→ 调用 `abortController.abort()`
3. SDK 的 `abortSignal` 传播到底层 LLM API 调用，真正取消生成
4. Gateway 持久化已缓冲的部分输出，广播 `state: "aborted"` 事件
5. 客户端保留已收到的部分文本，清空 run 状态

**核心机制：** Gateway 在调用 `dispatchInboundMessage` 时传入 `abortSignal: abortController.signal`（`src/gateway/server-methods/chat.ts:1113`），收到 cancel 时调用 `abortController.abort()`。

### Astron-Claw 的适配

Astron-claw 架构不同（SSE + Redis Stream 中转，非 WebSocket 直连），但取消原理一致：
- **OpenClaw SDK 已内建 `abortSignal` 支持。** `replyOptions` 接受 `abortSignal: AbortSignal`，信号传播到底层 AI agent runner
- 当前 astron-claw plugin **没有传递此参数**，这是需要修复的核心点
- cancel 信号需要通过 Redis Stream 中转（前端断开 SSE → Go 后端检测 → Redis → Plugin abort）

## 实现方案

### 1. Go 后端：新增 `SendCancelToBot` 方法

**文件:** `backend/internal/service/bridge.go`

在 `ConnectionBridge` 上新增方法，通过 Redis Stream 向 Bot 发送 `session/cancel` JSON-RPC 通知：

```go
func (b *ConnectionBridge) SendCancelToBot(ctx context.Context, token, sessionID string) error {
    rpcRequest := map[string]interface{}{
        "jsonrpc": "2.0",
        "method":  "session/cancel",
        "params":  map[string]interface{}{"sessionId": sessionID},
    }
    inbox := BotInboxPrefix + token
    data, _ := json.Marshal(map[string]interface{}{"rpc_request": rpcRequest})
    _, err := b.queue.Publish(ctx, inbox, string(data))
    return err
}
```

### 2. Go 后端：SSE 断开时触发取消

**文件:** `backend/internal/router/sse.go`

在 `chatSSE()` 的两个 `client_disconnect` 检测点（第 217-220 行和第 249-252 行），添加 `SendCancelToBot` 调用：

```go
case <-c.Request.Context().Done():
    closeReason = "client_disconnect"
    log.Info().Str("token", tp).Msg("SSE: client disconnected")
    // 通知 bot 取消当前 session 的生成
    go app.Bridge.SendCancelToBot(context.Background(), tokenStr, sessionID)
    return
```

注意用 `go` + `context.Background()` 异步发送，因为请求 context 已 Done。

### 3. Plugin：新增 `activeDispatches` Map 追踪 AbortController

**文件:** `plugin/src/runtime.ts`

新增一个 Map 追踪每个 session 活跃的 dispatch AbortController：

```typescript
// Map<sessionId, AbortController> — 追踪每个 session 的活跃 dispatch
export const activeDispatches = new Map<string, AbortController>();
```

### 4. Plugin：在 dispatch 时创建 AbortController 并传入 SDK

**文件:** `plugin/src/messaging/inbound.ts`

在 `handleJsonRpcPrompt` 中（第 356 行前后）：

1. 创建 `AbortController` 并存入 `activeDispatches`
2. 将 `abortController.signal` 传入 `replyOptions.abortSignal`（这是关键——让 SDK 内部真正中止 LLM 调用）
3. 在 finally 块中清理

```typescript
import { activeDispatches } from "../runtime.js";

// 在 dispatch 之前
const abortController = new AbortController();
activeDispatches.set(sessionId, abortController);

try {
  await rt.channel.reply.dispatchReplyWithBufferedBlockDispatcher({
    ctx,
    cfg,
    dispatcherOptions,
    replyOptions: {
      disableBlockStreaming: false,
      abortSignal: abortController.signal,  // <-- 关键：SDK 内建取消机制
      onPartialReply: async (payload: any) => {
        if (abortController.signal.aborted) return; // 提前退出
        // ...existing logic...
      },
    },
  });
  // ...existing post-dispatch logic...
} catch (err) {
  // 区分 abort 错误和其他错误
  if (abortController.signal.aborted) {
    logger.info(`Dispatch aborted for session ${sessionId}`);
    // 发送 done 事件让 SSE 流正常结束（如果有后续连接）
    if (!finalSent) sendFinal(allTurnsText + lastPartialText);
  } else {
    // ...existing error handling...
  }
} finally {
  activeDispatches.delete(sessionId);
  activeSessionCtx.delete(ctxKey);
  // ...existing cleanup...
}
```

### 5. Plugin：处理 `session/cancel` 消息

**文件:** `plugin/src/messaging/inbound.ts`

在 `handleInboundMessage` 函数中（第 41 行后），添加对 `session/cancel` 的处理：

```typescript
if (msg.jsonrpc === "2.0" && msg.method === "session/cancel") {
  const cancelSessionId = msg.params?.sessionId;
  if (cancelSessionId) {
    const ac = activeDispatches.get(cancelSessionId);
    if (ac) {
      logger.info(`Cancelling dispatch for session ${cancelSessionId}`);
      ac.abort();
    } else {
      logger.debug(`No active dispatch for session ${cancelSessionId}, cancel is no-op`);
    }
  }
  return;
}
```

### 6. 前端：停止后保留已接收的部分内容（已有，无需改动）

当前前端的 `stopStreaming()` 已经正确处理：
- abort fetch 后，`consumeSSE` 的 catch 忽略 `AbortError`
- 已接收的 chunks 保留在 `messages` 中
- `streaming` 设为 false，用户可立即发送新消息
- 对话历史完整保留（用户消息 + 部分 assistant 回复）

**无需改动前端代码。**

## 修改文件清单

| 文件 | 改动 |
|------|------|
| `backend/internal/service/bridge.go` | 新增 `SendCancelToBot` 方法 |
| `backend/internal/router/sse.go` | 两个 `client_disconnect` 点调用 `SendCancelToBot` |
| `plugin/src/runtime.ts` | 新增 `activeDispatches` Map |
| `plugin/src/messaging/inbound.ts` | 创建/传入 AbortController + 处理 `session/cancel` |

## 验证方案

1. **重新安装插件**（修改 plugin 代码后必须执行）:
   ```bash
   cd /home/hygao1024/astron-claw
   ./install.sh --bot-token sk-8bed7608668dc4f4b8b50d50cb4caed2d2279952ddd6ccc2 --server-url ws://129.211.5.25:8765/bridge/bot
   ```
2. **重启 Go 后端**: 重新编译并启动 backend
3. **测试打断**: 发送一个长回复的问题（如"写一篇 1000 字的文章"），在生成过程中点击停止按钮
4. **验证 Bot 端停止**: 检查 plugin 日志，应看到 `Cancelling dispatch for session ...` 和 `Dispatch aborted for session ...`
5. **验证前端**: 停止后部分内容保留在聊天窗口中
6. **验证继续对话**: 停止后立即发送新消息（如"hi"），应正常回复
7. **验证历史保留**: 问"我刚问了什么"，bot 应能回忆之前的对话（包括被打断的）
8. **验证正常流程不受影响**: 不打断时，对话应完全正常完成
