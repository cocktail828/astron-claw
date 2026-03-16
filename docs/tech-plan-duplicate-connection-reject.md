# 技术方案：Bridge Server 同 Token 重复建连拒绝（v3）

## 一、需求回顾

### 背景

插件侧存在 bug，可能对同一 bot token 发起多次 WebSocket 建连。当前服务端 `register_bot` 已有基础的重复拒绝逻辑，但存在以下不足：

| 现状 | 问题 |
|---|---|
| 本地判重依赖 `token in self._bots` | 仅覆盖同 worker 进程 |
| 跨 worker 判重依赖 `ZADD NX` + `ZSCORE` | 已实现，但无协议层快速探活，半开连接需等 45s 才被清理 |
| 应用层 `_BOT_SILENT_TTL=45s` 检测慢 | 检测窗口过长，其间其他 worker 持续拒绝合法新连接 |

### 核心需求

> 同一 token 的 bot，**首次建连成功且心跳持续正常**时，后续所有建连一律拒绝（返回 4002）。只有当已有连接**心跳失效**或**主动断开**后，才允许新连接。

### 约束

- 多 worker（Uvicorn multi-process）+ 集群部署，必须跨进程、跨节点判重
- 不引入驱逐（eviction）逻辑，纯拒绝策略
- 对现有架构侵入最小，能简化则简化

---

## 二、技术选型

| 组件 | 用途 | 理由 |
|---|---|---|
| **Uvicorn `ws_ping_interval/timeout`** | 协议层快速检测死连接（~20s） | 内置能力，零应用代码，替代应用层 `_BOT_SILENT_TTL` 检测 |
| **Redis ZSET** `bridge:bot_alive` | 跨 worker 连接注册 & 活性判断 | 已有，原子操作天然适合 |
| **`ConnectionBridge`** | 注册/注销/心跳管理 | 已有，本次简化其心跳逻辑 |

---

## 三、架构设计

### 多 Worker + 集群拓扑

```
              Node A                          Node B
        ┌──────────────────┐            ┌──────────────────┐
        │  Worker 1  W2    │            │  W3     W4       │
        │  _bots{}  _bots{}│            │ _bots{} _bots{}  │
        │  ws_ping 协议层   │            │ ws_ping 协议层   │
        │  10s/10s         │            │ 10s/10s          │
        └───────┬──────────┘            └───────┬──────────┘
                │                               │
                └──────── Redis ZSET ───────────┘
                        bridge:bot_alive
                     { token: score(now) }
```

### 双层检测机制

| 层级 | 机制 | 检测时间 | 作用范围 | 角色 |
|---|---|---|---|---|
| **L1 协议层** | Uvicorn `ws_ping_timeout` | ~20s | 本 worker | 快速检测死连接 -> 触发 `unregister_bot` -> ZREM |
| **L2 分布式** | ZSET score > `_BOT_TTL` + `_cleanup_expired_bots` | 30s | 集群全局 | 兜底清理幽灵条目 |

**移除**：应用层 `_BOT_SILENT_TTL` 检测（被 L1 完全替代）

### 设计原理

`ws_ping_timeout` 保证了 **`_bots` 中的 bot 在协议层确实存活**：

- bot 在 `_bots` 中 -> 协议层 ping 仍通 -> score 刷 `now` 是准确的
- bot 死亡 -> ~20s 协议层超时 -> WS 关闭 -> `unregister_bot` -> ZREM -> 新连接可接管
- 无需额外的 `_bot_last_seen` 追踪

### 建连判重流程

```
Plugin (WS Client)
    |
    v
+--------------------------------------------------------------+
|  WebSocket Handler (routers/websocket.py)                    |
|    1. Token 校验 (MySQL)                                     |
|    2. register_bot(token, ws)                                |
|    3. 成功 -> 进入消息循环 / 失败 -> 4002 + retry_after      |
+--------------------------------------------------------------+
                                   |
                                   v
+--------------------------------------------------------------+
|  ConnectionBridge.register_bot                               |
|    +- 本地: token in _bots -> reject                         |
|    +- Redis: ZADD NX -> success -> 注册成功                  |
|    +- Redis: ZADD NX -> fail:                                |
|         +- ZSCORE 在 _BOT_TTL 内 -> reject (连接存活)        |
|         +- ZSCORE 超出 _BOT_TTL -> ZADD GT 接管              |
+--------------------------------------------------------------+
```

### Bot 断网后完整时序

```
0s          10s          20s              30s
|-----------|------------|----------------|
Bot断网   心跳循环刷      ws_ping_timeout   _cleanup_expired_bots
          score=now      -> WS关闭          清理幽灵条目(兜底)
          (正确:尚未      -> unregister_bot
           检测到死亡)    -> ZREM
                          -> 新连接可接管
```

---

## 四、模块划分与改动范围

| 文件 | 改动类型 | 说明 |
|---|---|---|
| `server/services/bridge.py` | **简化** | 移除 `_bot_last_seen` 及 `_BOT_SILENT_TTL` 检测逻辑 |
| `server/routers/websocket.py` | **微调** | 移除 `mark_bot_seen` 调用；拒绝响应增加 `retry_after` |
| `server/run.py` | **微调** | 添加 `ws_ping_interval` / `ws_ping_timeout` |
| `server/infra/config.py` | **微调** | `ServerConfig` 增加两个配置项 |

---

## 五、接口设计

### WebSocket 建连拒绝响应（增强）

```json
{
  "error": "Bot already connected",
  "code": 4002,
  "retry_after": 30
}
```

随后关闭 WebSocket：`code=4002, reason="Bot already connected"`

| 字段 | 类型 | 说明 |
|---|---|---|
| `error` | string | 错误描述 |
| `code` | int | WebSocket close code |
| `retry_after` | int | 建议客户端重试等待秒数（= `_BOT_TTL`） |

---

## 六、数据模型

无新增。现有 Redis ZSET 不变：

```
Key:    bridge:bot_alive
Type:   Sorted Set
Member: <bot_token>
Score:  <unix_timestamp>   # 心跳循环刷 now，ws_ping_timeout 保证 _bots 准确性
```

---

## 七、关键设计决策

### 决策 1：拒绝 vs 驱逐

**选定拒绝策略。** 插件侧已修复重复建连 bug，服务端做防御性拒绝即可。

### 决策 2：移除 `_bot_last_seen` 及 `_BOT_SILENT_TTL`

| 维度 | 移除前（v2） | 移除后（v3） |
|---|---|---|
| 死连接检测 | 应用层 45s + 协议层 20s | 协议层 20s |
| ZSET score 刷新 | `_bot_last_seen[token]` | `now`（`ws_ping_timeout` 保证 `_bots` 准确） |
| 竞态防护 | 交集快照 `set(_bots) & set(_bot_last_seen)` | 不需要，幽灵条目由 `_cleanup_expired_bots` 30s 兜底 |
| 代码复杂度 | 三层检测 + 交集逻辑 | 两层检测，逻辑更简单 |

**`ws_ping_timeout` 使 `_bot_last_seen` 的核心用途全部冗余：**

- ~~超时检测~~：协议层 20s 更快
- ~~ZSET score 精度~~：`_bots` 中的 bot 经协议层确认存活，`now` 是准确的
- ~~竞态防护~~：简化为可接受的 30s 幽灵窗口

### 决策 3：幽灵条目的可接受性

**最坏场景**：`_run_heartbeat` ZADD 与 `unregister_bot` ZREM 交错，token 被写回 Redis。

**影响**：幽灵条目存活最长 `_BOT_TTL(30s)`，由已有的 `_cleanup_expired_bots` 清理。

**可接受**：
- 概率低（Python asyncio 单线程，竞态窗口极小）
- 影响有限（最多延迟新连接 30s，有 `retry_after` 引导客户端重试）
- 无需额外代码防护

### 决策 4：`_BOT_TTL` 保持 30s 不变

```
ws_ping 检测:  ~20s  (协议层断连 + ZREM)
ZSET 过期:     30s   (跨 worker 兜底)
                      |
              10s 余量覆盖幽灵条目场景
```

20s 协议层 + 30s ZSET 兜底，两层间有 10s 余量，配合合理。

---

## 八、实现计划

### Step 1：启用 Uvicorn 协议层 ws_ping

**文件**：`server/infra/config.py`

`ServerConfig` 增加两个可配置字段：

```python
@dataclass(frozen=True)
class ServerConfig:
    host: str
    port: int
    workers: int
    log_level: str
    access_log: bool
    ws_ping_interval: int    # 新增，默认 10
    ws_ping_timeout: int     # 新增，默认 10
```

`load_config` 中读取环境变量：

```python
ws_ping_interval=int(os.getenv("WS_PING_INTERVAL", "10")),
ws_ping_timeout=int(os.getenv("WS_PING_TIMEOUT", "10")),
```

**文件**：`server/run.py`

透传配置：

```python
uvicorn.run(
    "app:app",
    ...
    ws_ping_interval=server.ws_ping_interval,
    ws_ping_timeout=server.ws_ping_timeout,
)
```

### Step 2：简化 `_run_heartbeat`，移除 `_BOT_SILENT_TTL` 检测

**文件**：`server/services/bridge.py`

移除项：
- 常量 `_BOT_SILENT_TTL = 45`
- `_bot_last_seen` 字典
- `mark_bot_seen` 方法
- `_run_heartbeat` 中的 stale tokens 检测循环（L86-103）

简化后：

```python
async def _run_heartbeat(self) -> None:
    while not self._shutting_down:
        try:
            now = time.time()

            # ws_ping_timeout 保证 _bots 中的 bot 协议层存活，score=now 是准确的
            if self._bots:
                mapping = {token: now for token in self._bots}
                await self._redis.zadd(_BOT_ALIVE_KEY, mapping)

            # 分布式清理（不变）
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

### Step 3：移除 `websocket.py` 中的 `mark_bot_seen` 调用

**文件**：`server/routers/websocket.py`

```python
# Before:
raw = await ws.receive_text()
state.bridge.mark_bot_seen(bot_token)       # <- 移除
await state.bridge.handle_bot_message(bot_token, raw)

# After:
raw = await ws.receive_text()
await state.bridge.handle_bot_message(bot_token, raw)
```

### Step 4：建连拒绝响应增加 `retry_after`

**文件**：`server/routers/websocket.py`

```python
if not await state.bridge.register_bot(bot_token, ws):
    await ws.send_json({
        "error": Err.WS_DUPLICATE_BOT.message,
        "code": Err.WS_DUPLICATE_BOT.status,
        "retry_after": 30,
    })
    await ws.close(code=Err.WS_DUPLICATE_BOT.status, reason=Err.WS_DUPLICATE_BOT.message)
    return
```

### Step 5：`register_bot` 增强日志

**文件**：`server/services/bridge.py`

```python
async def register_bot(self, token: str, ws: WebSocket) -> bool:
    if token in self._bots:
        logger.info("[bridge] reject duplicate bot (local): {}...", token[:10])
        return False

    added = await self._redis.zadd(_BOT_ALIVE_KEY, {token: time.time()}, nx=True)
    if not added:
        score = await self._redis.zscore(_BOT_ALIVE_KEY, token)
        if score is not None and (time.time() - score) < _BOT_TTL:
            logger.info("[bridge] reject duplicate bot (remote, age={:.1f}s): {}...",
                        time.time() - score, token[:10])
            return False
        await self._redis.zadd(_BOT_ALIVE_KEY, {token: time.time()}, gt=True)

    self._bots[token] = ws
    # ... 后续不变
```

---

## 九、验证测试

| 场景 | 预期 |
|---|---|
| 同 token 双连接（同 worker） | 第二个被 4002 拒绝，响应含 `retry_after: 30` |
| 同 token 双连接（跨 worker） | 第二个被 4002 拒绝 |
| 首连接网络中断 | ~20s 协议层断开 + ZREM，新连接可接管 |
| 首连接网络中断（跨 worker 新建连） | ~20s ZREM 后，或 30s ZSET 过期后，新连接成功 |
| 幽灵条目 | `_cleanup_expired_bots` 30s 内清理，不影响后续建连 |

---

## 十、改动总结

| 项目 | 说明 |
|---|---|
| **改动量** | 净减代码 ~15 行（移除 > 新增），4 个文件 |
| **风险** | 低 -- 简化现有逻辑，协议层能力由 Uvicorn 保证 |
| **核心思路** | 用 `ws_ping_timeout` 替代 `_bot_last_seen` 的全部活性检测职责 |
| **多 worker 安全** | ZADD NX 原子判重 + `_cleanup_expired_bots` 幽灵兜底 |
| **向后兼容** | Node.js `ws` 库自动回复协议层 Pong，插件侧无需改动 |
