# 技术方案：Redis 键生命周期治理与注册模型简化

## 1. 技术方案概述

本方案解决当前 Redis 键设计中的两个核心问题：

| # | 问题 | 风险等级 | 方案 |
|---|---|---|---|
| 1 | `bridge:chat_inbox:{token}:{session_id}` Stream 永不删除，空 Stream + consumer group 元数据持续累积 | 高（内存泄漏） | SSE 流结束时 `DEL`，bot 断连时批量清理 |
| 2 | `online_bots` + `bot_workers` + `workers` + `worker_heartbeat` 四键体系过度复杂，引入了不必要的 worker_id 概念 | 中（复杂度） | 合并为单个 ZSET `bridge:bot_alive`，基于时间戳判活 |

## 2. 问题分析

### 2.1 Chat Inbox Stream 泄漏

**当前生命周期**：

```
创建: XADD / ensure_group(mkstream=True)   ← 每次 SSE 请求
清理: purge() (XTRIM MAXLEN 0)            ← 仅清空消息，空 Stream 驻留
删除: 无                                   ← ⚠️ 从不删除
```

**对比 Bot Inbox**（已正确处理）：

```
创建: register_bot() → ensure_group()
删除: unregister_bot() / shutdown() / heartbeat 清理 → delete_queue()  ← ✅
```

**泄漏估算**：每个空 Stream + 1 consumer group ≈ 200–300 bytes。百万 session → ~300MB，且无上限持续增长。

### 2.2 四键注册体系冗余

当前消息路由完全基于 token + session_id，**与 worker_id 无关**：

```
用户 → Bot:  任意 worker → XADD bot_inbox:{token} → 持有 WebSocket 的 worker 消费
Bot → 用户:  持有 WebSocket 的 worker → XADD chat_inbox:{token}:{sid} → SSE worker 消费
```

worker_id 唯一的实际用途是回答「此 token 的 bot 是否在线」。当前需要 3 跳查询：

```
SISMEMBER online_bots → HGET bot_workers → EXISTS worker:{id}
```

本质上只需要知道「bot 最后一次心跳是什么时候」，不需要知道它在哪个 worker 上。

### 2.3 当前 Redis 键清单（优化前）

| 键 | 类型 | TTL | 用途 | 问题 |
|---|---|---|---|---|
| `bridge:online_bots` | SET | 无 | 在线 bot token 集合 | 与 `bot_workers` 冗余 |
| `bridge:bot_workers` | HASH | 无 | token → worker_id | worker_id 概念不必要 |
| `bridge:workers` | SET | 无 | 已知 worker_id 集合 | worker_id 概念不必要 |
| `bridge:worker:{id}` | STRING | 30s | worker 心跳 | worker_id 概念不必要 |
| `bridge:bot_inbox:{token}` | STREAM | 无 | bot 收件箱 | 清理正常 ✅ |
| `bridge:chat_inbox:{token}:{sid}` | STREAM | 无 | chat 收件箱 | **从不删除** ⚠️ |

## 3. 技术选型

### 3.1 Bot 在线状态存储

| 方案 | 单点查询 | 统计总数 | 范围清理 | 千万级表现 |
|---|---|---|---|---|
| 独立 STRING key（per-token TTL） | O(1) | 需 SCAN ❌ | 需 SCAN ❌ | 不可行 |
| HASH（token → timestamp） | O(1) | O(N) ❌ 阻塞 | O(N) ❌ 阻塞 | 不可行 |
| **ZSET（score=timestamp）** | **O(log N)** | **O(log N)** ✅ | **O(log N + M)** ✅ | **全操作毫秒级** |

**选择 ZSET**：千万级 bot token 下，log N ≈ 23，所有操作均为毫秒级，不会阻塞 Redis。

### 3.2 Chat Inbox 清理策略

| 方案 | 优点 | 缺点 |
|---|---|---|
| Stream TTL | 全自动 | Redis 原生不支持 Stream TTL 自动清理 consumer group |
| **SSE finally 块 `DEL`** | **精确，流结束即清理** | 需兜底机制处理异常情况 |
| 定时 SCAN 清理 | 兜底兼容 | 不可作为主清理路径 |

**选择组合方案**：SSE finally 块作为主清理路径，heartbeat 中兜底清理残留。

## 4. 架构设计

### 4.1 优化后 Redis 键清单

| 键 | 类型 | 生命周期 | 用途 |
|---|---|---|---|
| `bridge:bot_alive` | **ZSET** | 永久（条目由锁持有者清理） | `score=unix_timestamp, member=token`，bot 在线状态 |
| `bridge:cleanup_lock` | **STRING** | NX + EX 10s（自动过期） | 清理任务分布式锁 |
| `bridge:bot_inbox:{token}` | STREAM | bot 断连时 `DEL` | bot 收件箱（不变） |
| `bridge:chat_inbox:{token}:{sid}` | STREAM | **SSE 流结束时 `DEL`** | chat 收件箱（新增清理） |

### 4.2 删除的键

| 键 | 类型 | 删除原因 |
|---|---|---|
| ~~`bridge:online_bots`~~ | SET | 被 ZSET 替代 |
| ~~`bridge:bot_workers`~~ | HASH | worker_id 概念移除 |
| ~~`bridge:workers`~~ | SET | worker_id 概念移除 |
| ~~`bridge:worker:{id}`~~ | STRING | 被 ZSET score 时间戳替代 |

### 4.3 ZSET 操作映射

```
bridge:bot_alive  ZSET  { score = unix_timestamp, member = token }
```

| 业务操作 | Redis 命令 | 复杂度 |
|---|---|---|
| bot 注册 | `ZADD bridge:bot_alive {now} {token}` | O(log N) |
| bot 断连 | `ZREM bridge:bot_alive {token}` | O(log N) |
| 心跳刷新 | `ZADD bridge:bot_alive {now} {token}`（覆盖 score） | O(log N) |
| bot 是否在线 | `ZSCORE bridge:bot_alive {token}` → `now - score < 30` | O(log N) |
| 在线 bot 总数 | `ZCOUNT bridge:bot_alive {now-30} +inf` | O(log N) |
| 枚举全部在线 bot | `ZRANGEBYSCORE bridge:bot_alive {now-30} +inf` | O(log N + K) |
| 清理过期条目 | `ZREMRANGEBYSCORE bridge:bot_alive -inf {now-30}` | O(log N + M) |
| 冲突检测 | `ZSCORE` → 判断是否过期 | O(log N) |

### 4.4 Heartbeat 职责分离

| 职责 | 频率 | 执行者 | 操作 |
|---|---|---|---|
| 心跳刷新 | 每 10s | **所有 worker** | `ZADD bridge:bot_alive {now} {token}` |
| 过期清理 | 每 10s（竞争） | **抢到锁的 1 个 worker** | 获取锁 → `ZRANGEBYSCORE` → 清理 inbox → `ZREMRANGEBYSCORE` |

**分布式锁设计**：

```
SET bridge:cleanup_lock {worker_id} NX EX 10
```

- `NX`：仅 key 不存在时设置成功 → 同一周期只有一个 worker 获取锁
- `EX=10`：锁自动过期，等于心跳间隔，无需手动释放
- worker 崩溃 → 锁自动过期 → 下一周期其他 worker 接管，不会死锁

## 5. 模块划分

### 5.1 修改范围

| 文件 | 修改类型 | 说明 |
|---|---|---|
| `server/services/bridge.py` | **重构** | 删除 4 个旧常量 + `_is_worker_alive()`，新增 ZSET 常量 + `_cleanup_expired_bots()` + `_cleanup_chat_inboxes()`，重写核心方法 |
| `server/routers/sse.py` | **增强** | SSE 流结束后 `delete_queue(inbox)` |
| `server/routers/admin.py` | 无变更 | `get_connections_summary()` 返回值格式不变 |
| `server/services/queue.py` | 无变更 | `delete_queue()` / `purge()` 已存在 |
| `server/tests/test_bridge.py` | **重写** | mock 从 SET/HASH/STRING 改为 ZSET |
| `server/tests/test_sse.py` | **增强** | 验证流结束后 inbox 被删除 |
| `server/tests/conftest.py` | **更新** | mock_redis 增加 ZSET 方法 |

### 5.2 不变的部分

- `MessageQueue` 抽象层（`queue.py`）— 接口不变
- `SessionStore`（`session_store.py`）— 不受影响
- 前端（`frontend/index.html`）— 不涉及 Redis 层
- Bot WebSocket 路由（`routers/websocket.py`）— 调用 `register_bot()` / `unregister_bot()` 接口不变

## 6. 接口设计

### 6.1 bridge.py 常量变更

```python
# ── 删除 ──
# _ONLINE_BOTS_KEY = "bridge:online_bots"
# _BOT_WORKERS_KEY = "bridge:bot_workers"
# _WORKERS_KEY = "bridge:workers"
# _WORKER_HEARTBEAT_PREFIX = "bridge:worker:"
# _WORKER_TTL = 30

# ── 新增 ──
_BOT_ALIVE_KEY = "bridge:bot_alive"           # ZSET: score=timestamp, member=token
_BOT_TTL = 30                                  # 超过此秒数未刷新视为离线
_CLEANUP_LOCK_KEY = "bridge:cleanup_lock"      # 分布式清理锁
_HEARTBEAT_INTERVAL = 10                       # 心跳刷新间隔（不变）
```

### 6.2 bridge.py 方法变更

| 方法 | 变更类型 | 说明 |
|---|---|---|
| `start()` | 简化 | 删除 `sadd(_WORKERS_KEY)`，仅启动 heartbeat |
| `_run_heartbeat()` | **重写** | 所有 worker：`ZADD` 刷新；竞争锁后清理过期 |
| `_is_worker_alive()` | **删除** | 不再需要 worker 存活检查 |
| `_cleanup_expired_bots()` | **新增** | 获取过期 token → 清理 bot_inbox + chat_inbox → ZREMRANGEBYSCORE |
| `_cleanup_chat_inboxes()` | **新增** | SCAN `bridge:chat_inbox:{token}:*` → 逐个 `DEL` |
| `register_bot()` | 简化 | 冲突检测：`ZSCORE` 判断 `now - score < _BOT_TTL`；注册：`ZADD` |
| `unregister_bot()` | 简化 | `ZREM` + `delete_queue(bot_inbox)` + 清理 chat_inbox |
| `is_bot_connected()` | 简化 | 单次 `ZSCORE` + 时间戳比较 |
| `get_connections_summary()` | 简化 | `ZRANGEBYSCORE` 获取全部在线 token |
| `remove_bot_sessions()` | 简化 | `ZREM` 替代 `SREM` + `HDEL` |
| `shutdown()` | 简化 | `ZREM` 每个本地 bot + 清理 inbox |

### 6.3 方法实现详情

#### `_run_heartbeat()` — 职责分离 + 分布式锁

```python
async def _run_heartbeat(self) -> None:
    """心跳循环：所有 worker 刷新本地 bot，竞争锁后清理过期条目。"""
    while not self._shutting_down:
        try:
            now = time.time()

            # ① 所有 worker：刷新本地 bot 心跳
            for token in self._bots:
                await self._redis.zadd(_BOT_ALIVE_KEY, {token: now})

            # ② 竞争锁，仅一个 worker 执行清理
            acquired = await self._redis.set(
                _CLEANUP_LOCK_KEY, self._worker_id,
                nx=True, ex=_HEARTBEAT_INTERVAL,
            )
            if acquired:
                await self._cleanup_expired_bots(now)

        except Exception:
            if not self._shutting_down:
                logger.exception("Heartbeat failed (worker={})", self._worker_id)
        await asyncio.sleep(_HEARTBEAT_INTERVAL)
```

#### `_cleanup_expired_bots()` — 锁持有者清理

```python
async def _cleanup_expired_bots(self, now: float) -> None:
    """清理过期 bot 及其关联的 inbox。仅由持锁 worker 调用。"""
    cutoff = now - _BOT_TTL
    expired = await self._redis.zrangebyscore(_BOT_ALIVE_KEY, "-inf", cutoff)
    if not expired:
        return

    for tok in expired:
        tok_str = tok if isinstance(tok, str) else tok.decode()
        await self._queue.delete_queue(f"{_BOT_INBOX_PREFIX}{tok_str}")
        await self._cleanup_chat_inboxes(tok_str)

    await self._redis.zremrangebyscore(_BOT_ALIVE_KEY, "-inf", cutoff)
    logger.info("Cleanup: removed {} expired bot(s)", len(expired))
```

#### `_cleanup_chat_inboxes()` — SCAN 清理 chat inbox

```python
async def _cleanup_chat_inboxes(self, token: str) -> None:
    """删除指定 token 的所有 chat inbox Stream。"""
    pattern = f"{CHAT_INBOX_PREFIX}{token}:*"
    async for key in self._redis.scan_iter(match=pattern, count=100):
        await self._redis.delete(key)
```

#### `register_bot()` — ZSCORE 冲突检测

```python
async def register_bot(self, token: str, ws: WebSocket) -> bool:
    if token in self._bots:
        return False

    # 检查是否有其他 worker 持有此 token 的活跃 bot
    score = await self._redis.zscore(_BOT_ALIVE_KEY, token)
    if score is not None and (time.time() - score) < _BOT_TTL:
        return False  # 另一个 worker 上的 bot 仍然活跃

    self._bots[token] = ws
    await self._redis.zadd(_BOT_ALIVE_KEY, {token: time.time()})
    inbox = f"{_BOT_INBOX_PREFIX}{token}"
    await self._queue.ensure_group(inbox, "bot")
    task_key = f"bot:{token}"
    self._poll_tasks[task_key] = asyncio.create_task(self._poll_bot_inbox(token))
    logger.info("Bot registered on worker {} (token={}...)", self._worker_id, token[:10])
    return True
```

#### `unregister_bot()` — ZREM + 清理 inbox

```python
async def unregister_bot(self, token: str) -> None:
    self._bots.pop(token, None)
    task_key = f"bot:{token}"
    task = self._poll_tasks.pop(task_key, None)
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    await self._redis.zrem(_BOT_ALIVE_KEY, token)
    await self._queue.delete_queue(f"{_BOT_INBOX_PREFIX}{token}")
    await self._cleanup_chat_inboxes(token)
    logger.info("Bot unregistered (worker={}, token={}...)", self._worker_id, token[:10])
```

#### `is_bot_connected()` — 1 次调用

```python
async def is_bot_connected(self, token: str) -> bool:
    score = await self._redis.zscore(_BOT_ALIVE_KEY, token)
    if score is None:
        return False
    return (time.time() - score) < _BOT_TTL
```

#### `get_connections_summary()` — 1 次调用

```python
async def get_connections_summary(self) -> dict[str, dict]:
    cutoff = time.time() - _BOT_TTL
    alive_tokens = await self._redis.zrangebyscore(
        _BOT_ALIVE_KEY, cutoff, "+inf",
    )
    return {
        (t if isinstance(t, str) else t.decode()): {"bot_online": True}
        for t in alive_tokens
    }
```

#### `remove_bot_sessions()` — ZREM 替代 SREM + HDEL

```python
async def remove_bot_sessions(self, token: str) -> None:
    await self._session_store.remove_sessions(token)
    if token in self._bots:
        bot_ws = self._bots[token]
        try:
            await bot_ws.close(code=4003, reason="Token deleted")
        except Exception:
            pass
        await self.unregister_bot(token)
    else:
        inbox = f"{_BOT_INBOX_PREFIX}{token}"
        await self._queue.publish(inbox, json.dumps({"_disconnect": True}))
    await self._redis.zrem(_BOT_ALIVE_KEY, token)
    await self._queue.delete_queue(f"{_BOT_INBOX_PREFIX}{token}")
    await self._cleanup_chat_inboxes(token)
    logger.info("Bot sessions fully removed (token={}...)", token[:10])
```

#### `shutdown()` — ZREM + 清理 inbox

```python
async def shutdown(self) -> None:
    self._shutting_down = True
    logger.info("Bridge worker {} shutting down...", self._worker_id)

    for token, ws in list(self._bots.items()):
        try:
            await ws.close(code=4000, reason="Server restarting")
        except Exception:
            pass
        await self._redis.zrem(_BOT_ALIVE_KEY, token)
        await self._queue.delete_queue(f"{_BOT_INBOX_PREFIX}{token}")
        await self._cleanup_chat_inboxes(token)
    self._bots.clear()
    self._pending_requests.clear()

    for task in self._poll_tasks.values():
        task.cancel()
    for task in self._poll_tasks.values():
        try:
            await task
        except asyncio.CancelledError:
            pass
    self._poll_tasks.clear()

    if self._heartbeat_task:
        self._heartbeat_task.cancel()
        try:
            await self._heartbeat_task
        except asyncio.CancelledError:
            pass

    logger.info("Bridge worker {} shutdown complete", self._worker_id)
```

### 6.4 sse.py — SSE 流结束后清理

```python
async def _stream_with_cleanup(token, session_id, session_number, req_id):
    """包装 _stream_response，在流结束后删除 chat inbox Stream。"""
    try:
        async for event in _stream_response(token, session_id, session_number, req_id):
            yield event
    finally:
        try:
            inbox = f"{CHAT_INBOX_PREFIX}{token}:{session_id}"
            await state.queue.delete_queue(inbox)
        except Exception:
            logger.warning("SSE: cleanup failed (token={}...)", token[:10])
```

在 `chat_sse()` 中将 `_stream_response` 替换为 `_stream_with_cleanup`：

```python
return StreamingResponse(
    _stream_with_cleanup(token, session_id, session_number, req_id),
    media_type="text/event-stream",
    ...
)
```

## 7. 数据模型

### 7.1 Redis 调用次数对比

| 操作 | 优化前（次） | 优化后（次） | 降幅 |
|---|---|---|---|
| `register_bot()` | 5 (SISMEMBER + HGET + EXISTS + SADD + HSET) | **2** (ZSCORE + ZADD) | -60% |
| `unregister_bot()` | 3 (SREM + HDEL + DEL) | **2** (ZREM + DEL) + SCAN 兜底 | -33% |
| `is_bot_connected()` | 3 (SISMEMBER + HGET + EXISTS) | **1** (ZSCORE) | -67% |
| `get_connections_summary()` | 3 轮 pipeline (SMEMBERS + N×HGET + M×EXISTS) | **1** (ZRANGEBYSCORE) | -67% |
| heartbeat（per cycle, N=本地 bot） | 2N + SMEMBERS + HGETALL + 逐个 EXISTS | **N** (ZADD) + 竞争锁 1 + 清理(摊销) | ~-50% |

### 7.2 内存占用对比

| 场景 | 优化前 | 优化后 |
|---|---|---|
| 1 万 bot | ~4 个键 + 1 万 heartbeat STRING | 1 个 ZSET（~640KB） |
| chat inbox（100 万 session） | 100 万空 Stream 驻留（~300MB） | 0（流结束即删除） |

## 8. 关键设计决策

| 决策点 | 选择 | 备选 | 理由 |
|---|---|---|---|
| 在线状态存储 | ZSET | HASH / 独立 STRING | 千万级全操作 O(log N)，避免阻塞 |
| 移除 worker_id | 是 | 保留 | 消息路由不依赖 worker_id，在线状态用时间戳自描述 |
| 清理任务协调 | Redis 分布式锁 (NX+EX) | 每个 worker 都清理 / Leader 选举 | 简单可靠，自动过期无死锁 |
| chat_inbox 主清理 | SSE finally 块 | TTL / 定时 SCAN | 最精确，流结束即回收 |
| chat_inbox 兜底清理 | heartbeat + `unregister_bot()` | 仅 SSE finally | 防御 SSE 异常退出或 bot 崩溃场景 |
| `_worker_id` 字段 | 保留（仅日志 + 锁标识） | 删除 | 日志追踪有价值，不参与状态管理 |
| SCAN 清理 chat_inbox | `scan_iter(match=prefix:*)` | 维护 per-token session SET | SCAN 范围限定在单 token，实际匹配数少（每 token 几个 session），无需额外索引键 |

## 9. 实现计划

| 步骤 | 内容 | 文件 | 依赖 |
|---|---|---|---|
| 1 | 替换常量：删除 4 个旧键常量，新增 `_BOT_ALIVE_KEY` + `_BOT_TTL` + `_CLEANUP_LOCK_KEY` | `bridge.py` | — |
| 2 | 新增 `_cleanup_expired_bots()` + `_cleanup_chat_inboxes()` 方法 | `bridge.py` | 步骤 1 |
| 3 | 重写 `_run_heartbeat()`：ZADD 刷新 + 竞争锁 + 调用清理 | `bridge.py` | 步骤 2 |
| 4 | 删除 `_is_worker_alive()` | `bridge.py` | 步骤 3 |
| 5 | 简化 `register_bot()`：ZSCORE 冲突检测 + ZADD 注册 | `bridge.py` | 步骤 1 |
| 6 | 简化 `unregister_bot()`：ZREM + 清理 chat_inbox | `bridge.py` | 步骤 2 |
| 7 | 简化 `is_bot_connected()` / `get_connections_summary()` / `remove_bot_sessions()` / `shutdown()` | `bridge.py` | 步骤 1 |
| 8 | 新增 `_stream_with_cleanup()`，SSE 流结束后 `delete_queue(inbox)` | `sse.py` | — |
| 9 | 更新 `conftest.py`：mock_redis 增加 ZSET 方法（`zadd`, `zrem`, `zscore`, `zrangebyscore`, `zremrangebyscore`, `zcount`, `scan_iter`） | `conftest.py` | — |
| 10 | 重写 `test_bridge.py`：适配 ZSET 接口 | `test_bridge.py` | 步骤 1–7 |
| 11 | 增强 `test_sse.py`：验证 inbox 清理 | `test_sse.py` | 步骤 8 |
| 12 | 全量单元测试 + E2E 集成测试 | — | 步骤 1–11 |
