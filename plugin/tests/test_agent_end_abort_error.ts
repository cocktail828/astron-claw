import { strict as assert } from "node:assert";

import { BOT_RPC_ERRORS } from "../src/constants.js";
import { registerToolHooks } from "../src/hooks.js";
import { activeSessionCtx, setLogger } from "../src/runtime.js";

class FakeApi {
  handlers = new Map<string, Array<(event: any, ctx: any) => void>>();

  on(eventName: string, handler: (event: any, ctx: any) => void): void {
    const list = this.handlers.get(eventName) ?? [];
    list.push(handler);
    this.handlers.set(eventName, list);
  }

  emit(eventName: string, event: any, ctx: any): void {
    const list = this.handlers.get(eventName) ?? [];
    for (const handler of list) {
      handler(event, ctx);
    }
  }
}

const sent: any[] = [];

setLogger({
  info: () => {},
  warn: () => {},
  error: () => {},
  debug: () => {},
});

activeSessionCtx.clear();

const api = new FakeApi();
registerToolHooks(api as any);

activeSessionCtx.set("agent:main:test:request-1", {
  bridgeClient: {
    send(payload: any): boolean {
      sent.push(payload);
      return true;
    },
  } as any,
  sessionId: "session-1",
  requestId: "request-1",
} as any);

api.emit("agent_end", {
  success: false,
  error: "aborted",
}, {
  sessionKey: "agent:main:test",
});

assert.equal(sent.length, 1, "agent_end aborted should send one bridge error");
assert.deepEqual(sent[0], {
  jsonrpc: "2.0",
  id: "request-1",
  sessionId: "session-1",
  error: BOT_RPC_ERRORS.AGENT_ABORTED_TIMEOUT,
});

activeSessionCtx.clear();

console.log("PASS test_agent_end_abort_error");
