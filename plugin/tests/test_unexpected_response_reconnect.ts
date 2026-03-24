import { strict as assert } from "node:assert";
import { EventEmitter } from "node:events";
import { setTimeout as sleep } from "node:timers/promises";

import { BridgeClient } from "../src/bridge/client.js";

class FakeWebSocket extends EventEmitter {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 3;

  readyState = FakeWebSocket.CONNECTING;
  destroyed = false;

  send(_data: string): void {}

  close(): void {
    this.readyState = FakeWebSocket.CLOSED;
    this.emit("close", 1000, Buffer.from(""));
  }

  reject(statusCode: number): void {
    this.emit("unexpected-response", {}, {
      statusCode,
      socket: {
        destroy: () => {
          this.destroyed = true;
        },
      },
    });
  }
}

const sockets: FakeWebSocket[] = [];

const client = new BridgeClient({
  url: "ws://test",
  token: "sk-test",
  logger: {
    info: (_msg: string) => {},
    warn: (_msg: string) => {},
    error: (_msg: string) => {},
  },
  retry: { baseMs: 10, maxMs: 50, maxAttempts: 0 },
  websocketFactory: () => {
    const ws = new FakeWebSocket();
    sockets.push(ws);
    return ws as unknown as any;
  },
});

client.start();
assert.equal(sockets.length, 1, "start() should create the first socket");

sockets[0].reject(502);
await sleep(30);

assert.equal(sockets[0].destroyed, true, "failed handshake socket should be destroyed");
assert.equal(sockets.length, 2, "502 should lead to a fresh reconnect attempt");
assert.equal(client.ws, sockets[1] as unknown as any, "new socket should become current immediately");

client.stop();

console.log("PASS test_unexpected_response_reconnect");
