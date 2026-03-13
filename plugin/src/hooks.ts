import { activeSessionCtx, pendingToolCtx, toolCtxKey, logger } from "./runtime.js";

// ---------------------------------------------------------------------------
// SDK event hooks (before_tool_call / after_tool_call)
// ---------------------------------------------------------------------------
export function registerToolHooks(api: any): void {
  // Hook: before_tool_call – send tool input to bridge
  api.on("before_tool_call", (event: any, ctx: any) => {
    // === DIAG: full before_tool_call event ===
    logger.info(`[DIAG] before_tool_call | tool=${event.toolName} sessionKey=${ctx.sessionKey} event=${JSON.stringify(event)}`);

    const sessionCtx = activeSessionCtx.get(ctx.sessionKey);
    if (!sessionCtx) return;
    // Stash for after_tool_call which lacks ctx.sessionKey (SDK bug)
    pendingToolCtx.set(toolCtxKey(event.toolName, event.params), { ...sessionCtx, _sk: ctx.sessionKey });
    const { bridgeClient, sessionId } = sessionCtx;
    const inputText = typeof event.params === "object"
      ? JSON.stringify(event.params) : String(event.params ?? "");
    bridgeClient.send({
      jsonrpc: "2.0",
      method: "session/update",
      params: {
        sessionId,
        update: {
          sessionUpdate: "tool_call",
          title: event.toolName || "tool",
          status: "running",
          content: inputText,
        },
      },
    });
  });

  // Hook: after_tool_call – send tool result to bridge
  // NOTE: SDK bug – ctx.sessionKey is undefined in after_tool_call,
  // so we look up via _pendingToolCtx keyed on toolName+params.
  api.on("after_tool_call", (event: any, ctx: any) => {
    // SDK fires after_tool_call twice; only handle the complete one (has durationMs)
    if (event.durationMs === undefined) return;

    // === DIAG: full after_tool_call event ===
    logger.info(`[DIAG] after_tool_call | tool=${event.toolName} durationMs=${event.durationMs} error=${event.error ?? 'none'} result=${JSON.stringify(event.result)}`);

    const key = toolCtxKey(event.toolName, event.params);
    const sessionCtx = activeSessionCtx.get(ctx.sessionKey) || pendingToolCtx.get(key);
    pendingToolCtx.delete(key); // cleanup
    if (!sessionCtx) return;
    const { bridgeClient, sessionId } = sessionCtx;
    const resultText = event.error
      ? `Error: ${event.error}`
      : (typeof event.result === "string" ? event.result : JSON.stringify(event.result ?? ""));
    bridgeClient.send({
      jsonrpc: "2.0",
      method: "session/update",
      params: {
        sessionId,
        update: {
          sessionUpdate: "tool_result",
          title: event.toolName || "tool",
          status: event.error ? "error" : "completed",
          content: resultText,
        },
      },
    });
  });

  // === DIAG: listen to ALL SDK events for full trace ===
  const trackedEvents = [
    "agent_start", "agent_end", "agent_error",
    "turn_start", "turn_end",
    "message_start", "message_end", "message_delta",
  ];

  // journald truncates lines at ~48 KB; split large payloads into chunks
  const LOG_CHUNK_SIZE = 30000;
  const logEvent = (tag: string, eventStr: string, ctxKeys: string): void => {
    if (eventStr.length <= LOG_CHUNK_SIZE) {
      logger.info(`[DIAG] SDK_EVENT ${tag} | event=${eventStr} ctx_keys=${ctxKeys}`);
      return;
    }
    const totalChunks = Math.ceil(eventStr.length / LOG_CHUNK_SIZE);
    logger.info(`[DIAG] SDK_EVENT ${tag} | total_length=${eventStr.length} chunks=${totalChunks} ctx_keys=${ctxKeys}`);
    for (let i = 0; i < totalChunks; i++) {
      logger.info(`[DIAG] SDK_EVENT ${tag} [${i + 1}/${totalChunks}] | ${eventStr.slice(i * LOG_CHUNK_SIZE, (i + 1) * LOG_CHUNK_SIZE)}`);
    }
  };

  for (const eventName of trackedEvents) {
    try {
      api.on(eventName, (event: any, ctx: any) => {
        logEvent(eventName, JSON.stringify(event), JSON.stringify(Object.keys(ctx ?? {})));
      });
    } catch {
      // event not supported, skip
    }
  }
}
