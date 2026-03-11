# 技术方案：多 Worker 架构缺陷修复

## 一、技术方案概述

### 1.1 背景

经全量审计，`ConnectionBridge` 在多 Worker（`uvicorn --workers N`）+ 集群部署下存在两类缺陷：

1. **`_pending_requests` 跨 Worker 不安全** — 进程内字典，映射 `request_id → (token, session_id)`，用于 JSON-RPC result/error 路由。跨 Worker 时查不到映射，导致 error 路由失败和内存泄漏。
2. **`register_bot()` 存在竞态条件** — check-then-set 非原子，两个 Worker 可能同时为同一 token 注册 bot 连接。

### 1.2 问题

#### 缺陷 1：`_pending_requests` 跨 Worker error 路由失败

```
Worker B (收到 SSE 请求)                    Worker A (持有 Bot WS)
─────────────────────                      ─────────────────────
send_to_bot():
  _pending_requests[req_id] = (tok, sid)
  XADD bot_inbox:{token} ──────────────►   _poll_bot_inbox():
                                              转发到 Bot WS

                                           handle_bot_message():
                                             _pending_requests.pop(req_id)
                                             → None  ← 查不到！在 Worker B 里

                                           error 事件无法路由 ❌
```

当 Bot 返回 JSON-RPC error 时，`handle_bot_message()` 在 Worker A 执行，但 `_pending_requests` 条目在 Worker B 的内存中。error 事件无法路由到 SSE 客户端，只能等待 5 分钟超时兜底。

**缺陷 2：`_pending_requests` 跨 Worker 内存泄漏**

Worker B 上的 `_pending_requests[req_id]` 永远不会被清除：
- `handle_bot_message()` 在 Worker A 执行，pop 不到
- `unregister_bot()` 在 Worker A 执行，只清理 Worker A 本地的条目
- 当前分支 `fix/tight-loop-and-memory-leak` 的修复（commit `11f4f2e`）仅解决了 Bot 所在 Worker 的泄漏，**未解决发起请求的 Worker 的泄漏**

#### 缺陷 3：`register_bot()` check-then-set 竞态

```python
# bridge.py:127-138 — 当前实现
async def register_bot(self, token: str, ws: WebSocket) -> bool:
    if token in self._bots:                              # 1. 本地检查
        return False
    score = await self._redis.zscore(_BOT_ALIVE_KEY, token)  # 2. Redis 检查
    if score is not None and (time.time() - score) < _BOT_TTL:
        return False
    self._bots[token] = ws                               # 3. 注册
    await self._redis.zadd(_BOT_ALIVE_KEY, {token: time.time()})
```

步骤 2（检查）和步骤 3（写入）不是原子操作，存在竞态窗口：

```
T1: Worker A  zscore(token) → None（无 bot）
T2: Worker B  zscore(token) → None（无 bot）   ← 两个 Worker 同时通过检查
T3: Worker A  zadd(token)   → 注册成功
T4: Worker B  zadd(token)   → 也注册成功        ← 同 token 两个 bot ❌
```

**后果**：同一 token 在两个 Worker 上各有一个 bot 连接，但 `bot_inbox:{token}` 只有一个 Stream，消息只会被其中一个 Worker 消费，另一个 bot 成为孤儿连接。

### 1.3 当前 `_pending_requests` 的实际用途

| 场景 | 代码位置 | 作用 | 跨 Worker 是否有效 |
|------|----------|------|:---:|
| JSON-RPC result 到达 | `bridge.py:322-325` | pop 出 session_id，**仅用于日志打印** | 否 |
| JSON-RPC error 到达 | `bridge.py:327-336` | pop 出 session_id，路由 error 到 chat inbox | 否 |

### 1.4 根因分析

Bot 返回 JSON-RPC result/error 时，消息体只有 `id`（即 request_id），**不携带 `sessionId`**。而通知型消息（chunk/done/thinking 等）通过 `params.sessionId` 直接携带了 session_id，无需查表。

这意味着只要让 Bot 在 result/error 中也回传 `sessionId`，就能从根本上消除对映射表的依赖。

### 1.5 目标

- 在 JSON-RPC result/error 协议中增加 `sessionId` 字段
- 移除 `_pending_requests` 字典及其所有生命周期管理代码
- 消除多 Worker 下的 error 路由失败和内存泄漏问题
- 修复 `register_bot()` 的 check-then-set 竞态，使用 `ZADD NX` 原子操作

### 1.6 影响范围

| 文件 | 变更类型 |
|------|----------|
| `plugin/src/messaging/inbound.ts` | **修改** — JSON-RPC result/error 中增加 `sessionId` 字段 |
| `server/services/bridge.py` | **修改** — `handle_bot_message()` 从消息中提取 sessionId，移除 `_pending_requests`；`register_bot()` 改用 `ZADD NX` 原子注册 |
| `docs/api.md` | **修改** — 5.4 节 JSON-RPC response 示例增加 `sessionId`；5.5 节 Python Bot 示例同步更新 |

---

## 二、技术选型

### 2.1 方案对比

| 方案 | 思路 | 优点 | 缺点 | 结论 |
|------|------|------|------|------|
| **A. 协议层回传 sessionId** | Bot 在 JSON-RPC response 中携带 sessionId | 零内存开销、彻底解决跨 Worker 问题、代码量减少 | 需 Plugin 和 Server 同步发布 | **推荐** |
| B. 迁移到 Redis | `HSET bridge:pending:{req_id}` + TTL | 不改 Plugin 协议 | 每次请求 +2 次 Redis 往返、需 TTL 防泄漏、用外部存储弥补协议缺陷 | 备选 |
| C. 保持现状 | 接受 error 路由不可靠 | 零改动 | 内存泄漏、error 路由失败、依赖超时兜底 | 不推荐 |

### 2.2 选择方案 A 的理由

1. **正确性**：从协议层面解决多 Worker 路由问题，不存在边界条件
2. **简洁性**：移除 `_pending_requests` 后减少约 30 行状态管理代码
3. **可维护性**：消除一整类内存泄漏风险
4. **改动量小**：Plugin 侧加一个字段，Server 侧取一个字段

---

## 三、架构设计

### 3.1 改造前消息流（error 路由依赖进程内映射）

```
Chat Client          Worker B (SSE)        Redis Streams       Worker A (Bot WS)        Bot
    │                     │                     │                     │                   │
    │── POST /chat ──────►│                     │                     │                   │
    │                     │ _pending_requests    │                     │                   │
    │                     │ [req_id]=(tok,sid)   │                     │                   │
    │                     │── XADD bot_inbox ───►│                     │                   │
    │                     │                     │──── XREADGROUP ────►│                   │
    │                     │                     │                     │── WS forward ────►│
    │                     │                     │                     │                   │
    │                     │                     │                     │◄── WS error ──────│
    │                     │                     │                     │ pop req_id → None ❌
    │                     │                     │                     │ 无法路由 error
    │                     │                     │                     │
    │  5min 超时 ◄────────│                     │                     │
```

### 3.2 改造后消息流（error 路由基于消息自带 sessionId）

```
Chat Client          Worker B (SSE)        Redis Streams       Worker A (Bot WS)        Bot
    │                     │                     │                     │                   │
    │── POST /chat ──────►│                     │                     │                   │
    │                     │── XADD bot_inbox ───►│                     │                   │
    │                     │                     │──── XREADGROUP ────►│                   │
    │                     │                     │                     │── WS forward ────►│
    │                     │                     │                     │                   │
    │                     │                     │                     │◄── WS error ──────│
    │                     │                     │                     │  {id, error,       │
    │                     │                     │                     │   sessionId: sid}  │
    │                     │                     │                     │                   │
    │                     │                     │◄─ XADD chat_inbox ─│ 直接从消息取 sid ✅│
    │                     │                     │                     │                   │
    │◄─ SSE error event ──│◄─ XREADGROUP ───────│                     │                   │
```

### 3.3 不变项

| 组件 | 说明 |
|------|------|
| `_bots: dict[str, WebSocket]` | 保持不变 — WebSocket 天然进程内 |
| `_poll_tasks` / `_heartbeat_task` | 保持不变 — asyncio Task 天然进程内 |
| Redis ZSET `bridge:bot_alive` | 保持不变 — 跨 Worker bot 存活检测 |
| Redis Streams `bot_inbox` / `chat_inbox` | 保持不变 — 跨 Worker 消息路由 |
| 通知型消息（chunk/done/thinking 等）路由 | 保持不变 — 已通过 `params.sessionId` 路由 |

---

## 四、接口设计

### 4.1 JSON-RPC 协议变更

#### JSON-RPC result（Bot → Server）

改造前：
```json
{
  "jsonrpc": "2.0",
  "id": "req_abc123",
  "result": { "stopReason": "end_turn" }
}
```

改造后：
```json
{
  "jsonrpc": "2.0",
  "id": "req_abc123",
  "result": { "stopReason": "end_turn" },
  "sessionId": "sid-xxx-yyy"
}
```

#### JSON-RPC error（Bot → Server）

改造前：
```json
{
  "jsonrpc": "2.0",
  "id": "req_abc123",
  "error": { "code": -32000, "message": "Dispatch not available" }
}
```

改造后：
```json
{
  "jsonrpc": "2.0",
  "id": "req_abc123",
  "error": { "code": -32000, "message": "Dispatch not available" },
  "sessionId": "sid-xxx-yyy"
}
```

> **注意**：`sessionId` 放在顶层而非 `result`/`error` 内部，因为它是路由元信息，不属于 RPC 语义载荷。

### 4.2 `register_bot()` 原子注册

改造前（check-then-set，非原子）：
```python
score = await self._redis.zscore(_BOT_ALIVE_KEY, token)
if score is not None and (time.time() - score) < _BOT_TTL:
    return False
self._bots[token] = ws
await self._redis.zadd(_BOT_ALIVE_KEY, {token: time.time()})
```

改造后（`ZADD NX` 原子操作）：
```python
added = await self._redis.zadd(
    _BOT_ALIVE_KEY, {token: time.time()}, nx=True,
)
if not added:
    # 另一个 Worker 已抢先注册，或旧心跳未过期
    score = await self._redis.zscore(_BOT_ALIVE_KEY, token)
    if score is not None and (time.time() - score) < _BOT_TTL:
        return False
    # 旧心跳已过期，强制覆盖
    await self._redis.zadd(_BOT_ALIVE_KEY, {token: time.time()})
self._bots[token] = ws
```

`ZADD NX` 由 Redis 单线程保证原子性——同一时刻只有一个 Worker 的 ZADD 会成功写入。失败的 Worker 再检查 score 是否过期：若未过期说明有活跃 bot，拒绝注册；若已过期则为清理残留场景，强制覆盖。

### 4.3 `handle_bot_message()` 接口变更

改造前：
```python
# 依赖 _pending_requests 查找 session_id
if "id" in msg and "error" in msg:
    info = self._pending_requests.pop(msg["id"], None)
    session_id = info[1] if info else None
```

改造后：
```python
# 直接从消息中提取 session_id
if "id" in msg and "error" in msg:
    session_id = msg.get("sessionId")
```

---

## 五、数据模型

### 5.1 移除的数据结构

```python
# bridge.py — 删除
self._pending_requests: dict[str, tuple[str, str]] = {}
```

### 5.2 不涉及数据库/Redis 变更

本方案仅涉及进程内内存结构和 WebSocket 协议的变更，不涉及：
- MySQL 表结构变更
- Redis Key 变更
- Alembic 迁移

---

## 六、关键设计决策

### 6.1 为什么不放在 `result`/`error` 内部

`sessionId` 是路由元信息，与 RPC 的业务返回值无关。放在顶层：
- 语义清晰：不污染 `result`/`error` 的业务含义
- 解析统一：无论 result 还是error，`msg.get("sessionId")` 一行搞定

### 6.2 多 Worker 安全性全量审计结论

| 变量 | 位置 | 安全？ | 理由 |
|------|------|:---:|------|
| `_worker_id` | `bridge.py:54` | 安全 | 设计就是 per-worker 唯一标识 |
| `_bots` | `bridge.py:56` | 竞态 | WebSocket 天然进程内；但 `register_bot()` check-then-set 非原子 → **本方案修复目标** |
| `_poll_tasks` | `bridge.py:66` | 安全 | asyncio Task 天然进程内 |
| `_heartbeat_task` | `bridge.py:68` | 安全 | 同上 |
| `_shutting_down` | `bridge.py:69` | 安全 | 本地 shutdown 协调 |
| `_redis` (cache.py) | `cache.py:6` | 安全 | per-worker 独立初始化 |
| `_engine` / `_session_factory` | `database.py:16-17` | 安全 | per-worker 独立连接池 |
| S3 client / aiohttp session | `storage/` | 安全 | per-worker 生命周期 |
| `state.*` 全局单例 | `state.py` | 安全 | per-worker lifespan 初始化 |
| **`_pending_requests`** | **`bridge.py:58`** | **不安全** | **本方案修复目标** |

---

## 七、实现计划

| 阶段 | 任务 | 涉及文件 | 说明 |
|------|------|----------|------|
| **P1** | Plugin 侧：JSON-RPC result/error 中回传 `sessionId` | `plugin/src/messaging/inbound.ts` | 4 处 `bridgeClient.send()` 调用添加 `sessionId` 字段（362-367 行、370-374 行、378-382 行、386-390 行） |
| **P2** | Server 侧：`handle_bot_message()` 从消息中提取 `sessionId`，同步移除 `_pending_requests` 及所有生命周期代码 | `server/services/bridge.py` | result/error 分支改为 `msg.get("sessionId")`；删除：字典声明(L58)、send_to_bot 中的注册(L251)/清理(L265,L286)、unregister_bot 中的清理(L150-153)、shutdown 中的 clear(L426) |
| **P3** | Server 侧：`register_bot()` 改用 `ZADD NX` 原子注册 | `server/services/bridge.py` | 替换 check-then-set 为 `ZADD NX` + 过期检查 fallback |
| **P4** | 更新 API 文档 | `docs/api.md` | 5.4 节 JSON-RPC response 示例增加 `sessionId` 字段；5.5 节 Python Bot 示例同步；更新注释说明 |
| **P5** | 更新测试 | `server/tests/test_bridge.py` | 移除 `_pending_requests` 相关断言，增加 sessionId 路由测试，增加 `register_bot` 并发注册测试 |
| **P6** | E2E 验证 | — | 多 Worker 部署下测试 error 路由和 bot 注册竞态 |

---

确认后可进入「代码实现」阶段。
