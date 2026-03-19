# 技术方案：WebSocket 重复连接踢旧机制

## Context

多 Worker + Redis Cluster 部署下，Bot 半开连接导致 token 被锁 45s，重连全部被拒。

**约束条件**：
- Redis Cluster 模式 — 不用 Pub/Sub（Cluster failover/rebalancing 时不可靠）
- 滚动更新 — 新旧 Worker 共存，需向后兼容
- 首版本不改插件代码

**核心策略**：从"拒绝新连接"改为"新连接踢掉旧连接"，跨 Worker 驱逐通过 **Generation 计数器 + Poll 轮询自检** 实现（不依赖 Pub/Sub）。

---

## 1. 方案概述

### 1.1 新增 Redis 键

| Key | Type | 用途 | Cluster 兼容 |
|-----|------|------|-------------|
| `bridge:bot_gen:{token}` | STRING (int) | 每次注册 INCR，单调递增代际号 | ✓ key 含 token，自然路由到正确 slot |

### 1.2 跨 Worker 驱逐信号传递

**不用 Pub/Sub**。两条检测路径：

| 检测点 | 时机 | 延迟 | 作用 |
|--------|------|------|------|
| `_poll_bot_inbox` 每轮循环检查 gen | 每次 XREADGROUP 返回（最多 5s） | ≤5s | Poll task 自我终止，停止消费消息 |
| `_run_heartbeat` 检查 gen | 每 10s | ≤10s | 兜底：清理本地 `_bots`、关闭旧 WS |

### 1.3 不改插件的影响

| 场景 | 影响 |
|------|------|
| 半开连接（常见） | 无影响 — 旧 WS 是半开的，close 帧到不了插件，插件独立检测后重连 |
| 两个插件实例用同 token（误配置） | 与现有行为一致 — 旧版被 4002 拒后重试，新版被踢后重试，指数退避收敛 |

---

## 2. 详细设计

### 2.1 注册流程 — `register_bot` 重写

```python
async def register_bot(self, token: str, ws: WebSocket) -> bool:
    # ① 同 Worker 驱逐：本地已持有 → 踢掉
    if token in self._bots:
        await self._evict_local(token)

    # ② 原子递增 generation
    gen = await self._redis.incr(f"{_BOT_GEN_PREFIX}{token}")

    # ③ 无条件写入 ZSET（不再用 NX）
    await self._redis.zadd(_BOT_ALIVE_KEY, {token: time.time()})

    # ④ 本地注册
    self._bots[token] = ws
    self._bot_gens[token] = gen

    # ⑤ 启动 poll task（传入 gen）
    inbox = f"{_BOT_INBOX_PREFIX}{token}"
    await self._queue.ensure_group(inbox, "bot")
    task_key = f"bot:{token}"
    old_task = self._poll_tasks.pop(task_key, None)
    if old_task:
        old_task.cancel()
    self._poll_tasks[task_key] = asyncio.create_task(
        self._poll_bot_inbox(token, gen)
    )

    # ⑥ 永远返回 True
    return True
```

**关键**：`INCR` 是原子操作，两个 Worker 竞争注册会得到不同 gen，高者胜出。

### 2.2 本地驱逐 — `_evict_local` (新增)

```python
async def _evict_local(self, token: str) -> None:
    """清理本地状态 + 关闭旧 WS，不动 Redis 键。"""
    ws = self._bots.pop(token, None)
    self._bot_gens.pop(token, None)

    task_key = f"bot:{token}"
    task = self._poll_tasks.pop(task_key, None)
    if task:
        task.cancel()
        try: await task
        except asyncio.CancelledError: pass

    if ws:
        try: await ws.close(code=4005, reason="Evicted by newer connection")
        except Exception: pass

    self.notify_bot_disconnected(token)
```

**不删 Redis 键**（ZSET、inbox stream、chat inbox）— 新 Worker 已接管。

### 2.3 注销流程 — `unregister_bot` 重写

```python
async def unregister_bot(self, token: str, ws: WebSocket | None = None) -> None:
    # Guard 1: WS 身份检查（同 Worker 防 stale finally）
    current_ws = self._bots.get(token)
    if ws is not None and current_ws is not ws:
        return

    local_gen = self._bot_gens.get(token)

    # 本地清理
    self._bots.pop(token, None)
    self._bot_gens.pop(token, None)

    task_key = f"bot:{token}"
    task = self._poll_tasks.pop(task_key, None)
    if task:
        task.cancel()
        try: await task
        except asyncio.CancelledError: pass

    self.notify_bot_disconnected(token)

    # Guard 2: Redis 清理前检查 generation — 防止破坏新 Worker 的状态
    remote_gen_raw = await self._redis.get(f"{_BOT_GEN_PREFIX}{token}")
    remote_gen = int(remote_gen_raw) if remote_gen_raw else None

    if remote_gen is not None and local_gen is not None and remote_gen > local_gen:
        logger.info("Skip Redis cleanup: newer gen exists remote={} local={} (token={}...)",
                     remote_gen, local_gen, token[:10])
        return

    # 安全清理 Redis
    await self._redis.zrem(_BOT_ALIVE_KEY, token)
    await self._queue.delete_queue(f"{_BOT_INBOX_PREFIX}{token}")
    await self._cleanup_chat_inboxes(token)
    logger.info("Bot unregistered (worker={}, token={}...)", self._worker_id, token[:10])
```

**这解决了核心安全问题**：旧 Worker 的 `finally` 块不会误删新 Worker 的 inbox stream。

### 2.4 Poll Task 自检驱逐

```python
async def _poll_bot_inbox(self, token: str, gen: int = 0) -> None:
    inbox = f"{_BOT_INBOX_PREFIX}{token}"
    while not self._shutting_down:
        try:
            result = await self._queue.consume(
                inbox, group="bot", consumer="bot",
                block_ms=_CONSUME_BLOCK_MS,
            )

            # ★ 每轮检查是否已被更新的 gen 取代
            if gen > 0:
                remote_gen_raw = await self._redis.get(f"{_BOT_GEN_PREFIX}{token}")
                if remote_gen_raw and int(remote_gen_raw) > gen:
                    logger.info("Poll task evicted: remote_gen={} > local_gen={} (token={}...)",
                                int(remote_gen_raw), gen, token[:10])
                    await self._evict_local(token)
                    break

            if result is None:
                await asyncio.sleep(1)
                continue

            msg_id, raw = result
            data = json.loads(raw)
            await self._queue.ack(inbox, "bot", msg_id)
            await self._queue.delete_message(inbox, msg_id)

            if data.get("_disconnect"):
                # ... 同现有逻辑
                break

            bot_ws = self._bots.get(token)
            if bot_ws:
                await bot_ws.send_json(data["rpc_request"])
            else:
                logger.warning("Inbox: bot WS gone, message dropped (token={}...)", token[:10])
        except asyncio.CancelledError:
            break
        except Exception:
            if not self._shutting_down:
                logger.exception("Bot inbox consume error (token={}...)", token[:10])
                await asyncio.sleep(1)
```

**当 gen=0（旧版 Worker 调用）时跳过检查** — 兼容滚动更新。

### 2.5 心跳中的 Gen 检查（兜底）

```python
async def _run_heartbeat(self) -> None:
    while not self._shutting_down:
        try:
            now = time.time()

            # ★ 检查本地 bot 是否被更新的 gen 取代
            for token, local_gen in list(self._bot_gens.items()):
                remote_gen_raw = await self._redis.get(f"{_BOT_GEN_PREFIX}{token}")
                if remote_gen_raw and int(remote_gen_raw) > local_gen:
                    logger.info("Heartbeat eviction: remote_gen={} > local_gen={} (token={}...)",
                                int(remote_gen_raw), local_gen, token[:10])
                    await self._evict_local(token)

            # 刷新心跳（仅刷新仍存活的 bot）
            if self._bots:
                mapping = {token: now for token in self._bots}
                await self._redis.zadd(_BOT_ALIVE_KEY, mapping)

            # 竞争清理锁
            # ... 同现有逻辑
        except Exception:
            ...
        await asyncio.sleep(_HEARTBEAT_INTERVAL)
```

**已删除**：`_bot_last_seen` 半开检测段，由 `ws_ping` + gen 检查完全替代。

### 2.6 ws_bot 端点简化

```python
@router.websocket("/bridge/bot")
async def ws_bot(ws: WebSocket, token: str = Query(default="")):
    bot_token = token or (ws.headers.get("x-astron-bot-token", ""))
    if not await state.token_manager.validate(bot_token):
        await ws.accept()
        await ws.close(code=Err.WS_INVALID_TOKEN.status, reason=Err.WS_INVALID_TOKEN.message)
        return

    await ws.accept()

    # register_bot 永远成功（踢旧逻辑在内部处理）
    await state.bridge.register_bot(bot_token, ws)

    state.bridge.notify_bot_connected(bot_token)
    try:
        while True:
            raw = await ws.receive_text()
            await state.bridge.handle_bot_message(bot_token, raw)
    except WebSocketDisconnect:
        logger.info("Bot disconnected: {}...", bot_token[:10])
    except Exception:
        logger.exception("Bot connection error: {}...", bot_token[:10])
    finally:
        await state.bridge.unregister_bot(bot_token, ws)
```

移除 `if not register_bot` 的 4002 拒绝分支，移除 `mark_bot_seen()` 调用。

---

## 3. 滚动更新兼容性

| 场景 | 旧 Worker → 新 Worker | 新 Worker → 旧 Worker |
|------|----------------------|----------------------|
| 旧持有连接，新收到重连 | 新 Worker `register_bot` 成功（INCR + ZADD 无条件），旧 Worker 的 poll task 在 ≤5s 内检测到 gen 变化自行退出 | 不会发生 — 旧 Worker 仍用旧 reject 逻辑，**此场景下旧行为不变**（可接受，随更新比例减少） |
| 旧 Worker 的 finally 块 | 旧 Worker 无 `_bot_gens`，`local_gen=None` → gen guard 条件 `remote_gen > local_gen` 不满足（None 比较跳过）→ 走旧的全量清理 | — |

**旧 Worker finally 的安全处理**：当 `local_gen is None`（旧版 Worker 没有 `_bot_gens`），`unregister_bot` 中的 gen guard 不会触发，走完整 Redis 清理。这可能误删新 Worker 的 inbox。

**缓解**：新 Worker 的 poll task 有 NOGROUP 自恢复机制（`queue.py:133-136`），stream 被删后自动重建。消息丢失窗口仅限于旧 Worker finally 执行到新 Worker 重建 stream 之间（毫秒级）。**全量更新完成后此问题消失**。

---

## 4. 竞态分析

| 竞态 | 保护机制 | 结果 |
|------|---------|------|
| 两个 Worker 同时注册同 token | INCR 原子递增，各得不同 gen，高者胜 | ✓ 低 gen Worker 被 poll/heartbeat 驱逐 |
| 旧 Worker finally 先于新 Worker INCR | remote_gen == local_gen → 合法清理；新 Worker 后续 INCR+ensure_group 重建 | ✓ 无冲突 |
| 旧 Worker finally 晚于新 Worker INCR | remote_gen > local_gen → 跳过 Redis 清理 | ✓ 新状态不被破坏 |
| Poll task 消费到消息但 WS 半开 | send_json 失败，消息丢失（与修复前一致） | ✓ 不引入新问题 |

---

## 5. 文件变更清单

### `server/infra/errors.py`
- 新增 `WS_EVICTED = (4005, "Evicted by newer connection")`

### `server/run.py`
- 新增 `ws_ping_interval=10`, `ws_ping_timeout=10`

### `server/services/bridge.py` (核心)
- 新增常量：`_BOT_GEN_PREFIX = "bridge:bot_gen:"`
- 新增实例变量：`_bot_gens: dict[str, int] = {}`
- 新增方法：`_evict_local(token)` — 仅清本地，不动 Redis
- 重写：`register_bot()` — 踢旧 → INCR → ZADD → return True
- 重写：`unregister_bot()` — generation guard 保护 Redis 清理
- 修改：`_poll_bot_inbox(token, gen)` — 每轮 gen 自检
- 修改：`_run_heartbeat()` — gen 检查兜底，删除 `_bot_last_seen` 半开检测段
- 修改：`_cleanup_expired_bots()` — 清理 gen 键
- 修改：`shutdown()` — 清理 `_bot_gens` + gen 键
- 删除：`_bot_last_seen` dict、`mark_bot_seen()` 方法、`_BOT_SILENT_TTL` 常量（由 ws_ping 替代）

### `server/routers/websocket.py`
- 移除 `register_bot` 返回 False 的 4002 拒绝分支
- 移除 `mark_bot_seen()` 调用（由 ws_ping 替代）

### `server/tests/test_duplicate_token.py`
- 更新验证：WS#2 应立即连接成功

---

## 6. 检测时效对比

| 场景 | 修复前 | 修复后 |
|------|--------|--------|
| Bot 重连 | **被拒 45s** | **立即成功 (0s)** |
| 旧 Worker 本地清理 | — | ≤5s (poll 自检) / ≤10s (heartbeat 兜底) |
| 无重连的半开连接 | 45s | ~20s (ws_ping) |

---

## 7. 实现顺序

1. `server/infra/errors.py` — 添加 WS_EVICTED
2. `server/run.py` — 添加 ws_ping
3. `server/services/bridge.py` — 核心踢旧机制
4. `server/routers/websocket.py` — 移除拒绝分支
5. `server/tests/test_duplicate_token.py` — 验证修复

---

## 8. 验证方案

复用已有 `tests/test_duplicate_token.py`（iptables DROP）：

1. `SERVER_WORKERS=2 uv run python run.py`
2. WS#1 建连 → iptables DROP → WS#2 重连
3. **预期**：WS#2 **立即成功**（Attempt 1 即通过）
4. 等待 5-10s，检查服务端日志：`Poll task evicted` 或 `Heartbeat eviction`
5. Redis 验证：`GET bridge:bot_gen:{token}` 值 ≥ 2
