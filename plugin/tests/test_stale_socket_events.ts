import { strict as assert } from "node:assert";
import { EventEmitter } from "node:events";
import { setTimeout as sleep } from "node:timers/promises";

import { BridgeClient } from "../src/bridge/client.js";

class FakeWebSocket extends EventEmitter {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 3;

  readyState = FakeWebSocket.CONNECTING;

  send(_data: string): void {}

  close(): void {
    this.readyState = FakeWebSocket.CLOSED;
    this.emit("close", 1000, Buffer.from(""));
  }

  open(): void {
    this.readyState = FakeWebSocket.OPEN;
    this.emit("open");
  }

  failHandshake(message: string): void {
    this.emit("error", new Error(message));
    this.readyState = FakeWebSocket.CLOSED;
    this.emit("close", 1006, Buffer.from(""));
  }
}

const sockets: FakeWebSocket[] = [];
const logs: string[] = [];

const client = new BridgeClient({
  url: "ws://test",
  token: "sk-test",
  logger: {
    info: (msg: string) => logs.push(`INFO ${msg}`),
    warn: (msg: string) => logs.push(`WARN ${msg}`),
    error: (msg: string) => logs.push(`ERROR ${msg}`),
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
sockets[0].open();
assert.equal(client.isReady(), true, "first socket should make client ready");

// Simulate a second connect attempt becoming current before the first socket
// later reports a stale handshake timeout / close.
(client as any).ws = null;
(client as any)._connect();
assert.equal(sockets.length, 2, "manual second connect should create another socket");
sockets[1].open();
assert.equal(client.ws, sockets[1] as unknown as any, "second socket should be current");

sockets[0].failHandshake("Opening handshake has timed out");
await sleep(30);

assert.equal(sockets.length, 2, "stale socket events must not trigger a third socket");
assert.equal(client.ws, sockets[1] as unknown as any, "newer socket should remain current");
assert.equal(client.isReady(), true, "client should stay ready after stale socket failure");

client.stop();

console.log("PASS test_stale_socket_events");
for (const line of logs) {
  console.log(line);
}
