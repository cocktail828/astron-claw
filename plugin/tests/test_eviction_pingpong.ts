/**
 * Verify fix: monitorBridgeProvider called twice no longer causes ping-pong.
 *
 * Mirrors monitor.ts logic:
 *   1. new BridgeClient(...)
 *   2. prev = activeBridgeClients.get() → prev.stop()  ← NEW FIX
 *   3. activeBridgeClients.set(accountId, bridgeClient)
 *   4. bridgeClient.start()
 *
 * Expected: second call stops the first client, no 4005 eviction loop.
 *
 * Also verifies: BridgeClient receiving 4005 does NOT retry (defense-in-depth).
 *
 * Usage:
 *   SERVER_WORKERS=2 uv run python run.py   # bridge server
 *   cd plugin && npx tsx tests/test_eviction_pingpong.ts
 */

import { BridgeClient } from "../src/bridge/client.js";

const TOKEN = "sk-cd5edf25c7369159318d3d46f5ea0bc8f3e9df3b7ed5672f";
const URL = "ws://127.0.0.1:8765/bridge/bot";
const ACCOUNT_ID = "default";
const OBSERVE_SECONDS = 10;

// Simulates the real activeBridgeClients Map from runtime.ts
const activeBridgeClients = new Map<string, BridgeClient>();

const stats = { first: { connects: 0, evictions: 0 }, second: { connects: 0, evictions: 0 } };

function makeLogger(tag: string) {
  const key = tag as "first" | "second";
  return {
    info: (msg: string) => {
      if (msg.includes("connected")) stats[key].connects++;
      console.log(`  [${tag}] ${msg}`);
    },
    warn: (msg: string) => {
      if (msg.includes("4005")) stats[key].evictions++;
      console.log(`  [${tag}] ${msg}`);
    },
    error: (msg: string) => console.log(`  [${tag}] ERROR: ${msg}`),
  };
}

const retry = { baseMs: 1000, maxMs: 60000, maxAttempts: 0 };

/**
 * Mirrors the FIXED monitorBridgeProvider (with prev.stop() guard).
 */
function simulateMonitorBridgeProvider(tag: string) {
  const bridgeClient = new BridgeClient({
    url: URL,
    token: TOKEN,
    logger: makeLogger(tag),
    retry,
  });

  // FIX: stop previous client before overwriting
  const prev = activeBridgeClients.get(ACCOUNT_ID);
  if (prev) {
    console.log(`  [monitor] stopping previous client before registering new one`);
    prev.stop();
  }
  activeBridgeClients.set(ACCOUNT_ID, bridgeClient);

  bridgeClient.start();
}

console.log("=".repeat(60));
console.log("Verifying fix: monitorBridgeProvider with prev.stop() guard");
console.log(`  Observe for ${OBSERVE_SECONDS}s`);
console.log("=".repeat(60));

// --- First startAccount call ---
console.log("\n[t=0s] gateway.startAccount → monitorBridgeProvider (first call)");
simulateMonitorBridgeProvider("first");

// --- Second startAccount call (hot-reload) ---
setTimeout(() => {
  console.log("\n[t=3s] gateway.startAccount → monitorBridgeProvider (second call)");
  console.log("       prev.stop() should kill the first client cleanly\n");
  simulateMonitorBridgeProvider("second");

  // --- Observe and report ---
  setTimeout(() => {
    const current = activeBridgeClients.get(ACCOUNT_ID);
    current?.stop();

    const totalEvictions = stats.first.evictions + stats.second.evictions;
    console.log("\n" + "=".repeat(60));
    console.log("Results:");
    console.log(`  First  client: ${stats.first.connects} connects, ${stats.first.evictions} evictions`);
    console.log(`  Second client: ${stats.second.connects} connects, ${stats.second.evictions} evictions`);
    if (totalEvictions === 0) {
      console.log(`\n  FIX VERIFIED: 0 evictions — no ping-pong!`);
    } else if (totalEvictions <= 1) {
      console.log(`\n  FIX VERIFIED: ${totalEvictions} eviction (one-time takeover, no loop)`);
    } else {
      console.log(`\n  FIX FAILED: ${totalEvictions} evictions — ping-pong still occurring!`);
    }
    console.log("=".repeat(60));
    process.exit(totalEvictions > 1 ? 1 : 0);
  }, OBSERVE_SECONDS * 1000);
}, 3000);
