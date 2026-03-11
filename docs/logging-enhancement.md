# 日志增强技术方案

## 1. 技术方案概述

### 背景

Astron Claw 当前使用 loguru 作为日志后端，全项目约 **90 处** logger 调用。经全量审计发现：

- **42 处**关键路径缺失日志
- **15 处** `except` 块静默吞掉异常（3 处为高危）
- **13 处**外部服务调用（Redis/MySQL/S3）无日志无计时
- **2 个**核心路由文件（tokens、metrics）零日志
- **1 个**核心服务文件（queue.py）导入 logger 但从未使用
- 无结构化日志输出（无 JSON 格式选项）
- 无启动配置摘要日志

### 目标

| 目标 | 衡量标准 |
|------|---------|
| 线上问题 5 分钟定位 | 任意错误请求均可通过日志追踪完整链路 |
| 零静默异常 | 所有 `except` 块至少有一条日志 |
| 外部调用可观测 | Redis/MySQL/S3 关键操作有成功+失败+耗时日志 |
| 请求可追踪 | 每个 API 请求的入口和结果有日志 |

### 约束

- 仅使用 loguru（已在用），不引入新日志框架
- 不改变现有日志输出格式和级别架构
- DEBUG 级别日志可添加但不影响默认 INFO 级别的日志量
- 最小侵入：不改已有日志内容，只增补缺失

---

## 2. 技术选型

| 项目 | 选型 | 理由 |
|------|------|------|
| 日志框架 | loguru（已用） | 项目已全面接入，API 简洁，支持结构化绑定 |
| 日志格式 | 文本（现有）+ JSON 可选 | 通过环境变量 `LOG_FORMAT_JSON=true` 切换 |
| 错误日志隔离 | 新增 error 文件 sink | 独立 `logs/error.log` 仅记录 WARNING+，便于告警 |
| 请求追踪 | FastAPI middleware + logger.bind | 注入 request_id 到上下文，无需改业务代码 |

---

## 3. 现状诊断与缺口清单

### 3.1 日志基础设施缺口

| 缺口 | 影响 | 优先级 |
|------|------|--------|
| 无 JSON 格式输出 | 无法接入 ELK/Loki 等日志聚合系统 | P1 |
| 无独立 error 日志文件 | 错误淹没在 info 中，不利于监控告警 | P1 |
| 无启动配置摘要 | 线上无法确认实际加载的配置 | P1 |
| 日志路径不可配置 | 容器部署不友好 | P2 |

### 3.2 静默异常（`except` 无日志）

| 文件 | 位置 | 风险 | 优先级 |
|------|------|------|--------|
| `session_store.py:144` | cleanup 中 Redis 错误被吞 | **高** — 缓存不一致 | P0 |
| `bridge.py:183` | admin 删 token 时 WS close 失败被吞 | 中 | P1 |
| `bridge.py:391` | 断连命令 WS close 失败被吞 | 中 | P1 |
| `queue.py:131-133` | NOGROUP 恢复无日志 | 中 | P1 |
| `queue.py:179` | BUSYGROUP 无日志 | 低 | P2 |
| `bridge.py:419,432,440` | shutdown 路径 | 低 | P2 |

### 3.3 零日志的核心路径

| 文件/模块 | 缺失内容 | 优先级 |
|-----------|---------|--------|
| `services/queue.py` | 全部 7 个方法零日志（Redis Streams 骨干） | P0 |
| `infra/storage/s3.py` `put_object` | S3 上传无日志无计时 | P0 |
| `routers/sse.py` 错误分支 | auth_fail/no_bot/session_not_found 返回错误但无日志 | P0 |
| `routers/admin.py` `_require_admin` | 未授权访问完全不可见 | P1 |
| `routers/tokens.py` | token 创建/验证零日志 | P1 |
| `routers/metrics.py` | metrics 操作零日志 | P2 |
| `services/token_manager.py` `validate` | 每次鉴权调用无日志 | P1 |
| `services/admin_auth.py` Redis 操作 | session CRUD 无日志 | P1 |
| `app.py` lifespan | 初始化各步骤无错误日志 | P1 |
| `infra/cache.py` | Redis 连接失败无日志 | P1 |
| `infra/config.py` | 启动配置不可见 | P1 |
| `infra/storage/__init__.py` | 选择的存储后端不可见 | P2 |

### 3.4 外部服务调用无计时

| 调用 | 文件 | 优先级 |
|------|------|--------|
| S3 `put_object` | `storage/s3.py` | P0 |
| iFlytek `put_object` | `storage/ifly_gateway.py` | P1 |
| MySQL 初始化+连接 | `database.py` | P1 |
| Redis 初始化+ping | `cache.py` | P1 |

---

## 4. 模块划分

改动按模块分组，每个模块独立可测、可单独合入。

| 模块 | 文件范围 | 改动类型 |
|------|---------|---------|
| **M1: 日志基础设施** | `infra/log.py` | 增强配置 |
| **M2: 启动链路** | `app.py`, `infra/config.py`, `infra/cache.py`, `infra/database.py`, `infra/storage/` | 补日志 |
| **M3: queue 核心** | `services/queue.py` | 补全 7 个方法的日志 |
| **M4: 鉴权链路** | `services/token_manager.py`, `services/admin_auth.py` | 补关键操作日志 |
| **M5: SSE 请求链路** | `routers/sse.py` | 补错误分支日志 |
| **M6: 路由层** | `routers/tokens.py`, `routers/admin.py`, `routers/metrics.py`, `routers/media.py` | 补请求日志 |
| **M7: 静默异常修复** | `services/bridge.py`, `services/session_store.py` | except 块加日志 |
| **M8: 存储层计时** | `infra/storage/s3.py`, `infra/storage/ifly_gateway.py` | 加耗时日志 |

---

## 5. 详细设计

### M1: 日志基础设施增强 — `infra/log.py`

#### 5.1.1 新增 JSON 格式输出

通过 `LOG_FORMAT_JSON` 环境变量切换。JSON 格式利于 ELK/Loki 等系统接入。

```python
def _json_serializer(message):
    record = message.record
    return json.dumps({
        "timestamp": record["time"].strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "level": record["level"].name,
        "module": record["name"],
        "function": record["function"],
        "line": record["line"],
        "message": record["message"],
        "extra": record["extra"],
    }, ensure_ascii=False) + "\n"
```

#### 5.1.2 新增 error 专用日志文件

```python
# logs/error.log — 仅 WARNING 及以上
logger.add(
    log_dir / "error.log",
    level="WARNING",
    format=file_format,
    rotation="50 MB",
    retention="30 days",
    compression="gz",
)
```

#### 5.1.3 日志路径可配置

```python
log_dir = Path(os.getenv("LOG_DIR", str(Path(__file__).resolve().parent.parent / "logs")))
```

#### 5.1.4 启动配置摘要

在 `setup_logging` 末尾输出：

```python
logger.info("Logging initialised: level={}, json={}, dir={}", level, use_json, log_dir)
```

### M2: 启动链路 — `app.py` + 基础设施

#### `app.py` lifespan 每步加 try/log

```python
# 当前（无错误日志）
await init_db(config.mysql)

# 改后
try:
    await init_db(config.mysql)
except Exception:
    logger.exception("Failed to initialise MySQL")
    raise
```

对 `init_db`、`init_redis`、`init_telemetry`、`run_migrations`、`create_storage`、`bridge.start` 每步均加此模式。

#### `infra/config.py` 启动配置摘要

在 `load_config()` 末尾增加摘要日志（脱敏）：

```python
logger.info(
    "Config loaded: redis={}:{} mysql={}:{}/{} workers={} otlp={}",
    c.redis.host, c.redis.port,
    c.mysql.host, c.mysql.port, c.mysql.database,
    c.server.workers, c.otlp.enabled,
)
```

#### `infra/cache.py`

```python
# init_redis 失败时
except Exception:
    logger.exception("Redis connection failed: {}:{}", config.host, config.port)
    raise
```

#### `infra/database.py`

```python
# _ensure_database 失败时
except Exception:
    logger.exception("Failed to ensure database '{}'", database)
    raise

# init_db SELECT 1 失败时
except Exception:
    logger.exception("MySQL connectivity check failed")
    raise
```

#### `infra/storage/__init__.py`

```python
logger.info("Storage backend: {} (endpoint={})", config.type, config.endpoint)
```

#### `infra/storage/s3.py`

```python
# start()
logger.info("S3 client initialised (endpoint={})", self._endpoint)

# close()
logger.info("S3 client closed")
```

#### `infra/storage/ifly_gateway.py`

```python
# start()
logger.info("iFlytek Gateway client initialised")

# close()
logger.info("iFlytek Gateway client closed")
```

### M3: queue 核心 — `services/queue.py`

Redis Streams 是系统消息骨干，全部方法补日志：

```python
async def publish(self, stream, data, ...):
    # ...
    logger.debug("Queue publish: stream={} msg_id={}", stream, msg_id)
    return msg_id

async def consume(self, stream, group, consumer, ...):
    # ...成功时
    logger.debug("Queue consume: stream={} group={} msg_id={}", stream, group, msg_id)
    # NOGROUP 恢复时
    logger.warning("Queue NOGROUP: stream={} group={}, recreated", stream, group)

async def ack(self, stream, group, msg_id):
    logger.debug("Queue ack: stream={} msg_id={}", stream, msg_id)

async def delete_message(self, stream, msg_id):
    logger.debug("Queue delete_message: stream={} msg_id={}", stream, msg_id)

async def delete_queue(self, stream):
    logger.debug("Queue delete_queue: stream={}", stream)

async def purge(self, stream):
    logger.debug("Queue purge: stream={}", stream)

async def ensure_group(self, stream, group):
    # BUSYGROUP 时
    logger.debug("Queue ensure_group: stream={} group={} (already exists)", stream, group)
```

> 全部使用 `debug` 级别（高频操作），NOGROUP 恢复用 `warning`。

### M4: 鉴权链路

#### `services/token_manager.py`

```python
async def validate(self, token):
    # 验证成功
    logger.debug("Token validated: {}...", token[:10])
    # 验证失败（过期或不存在）
    logger.debug("Token validation failed: {}...", token[:10])
```

> 使用 `debug`，因为每个请求都会调用。

#### `services/admin_auth.py`

```python
async def create_session(self, ...):
    logger.debug("Admin session created")

async def validate_session(self, session_token):
    # 成功
    logger.debug("Admin session validated")
    # 失败
    logger.debug("Admin session invalid or expired")

async def remove_session(self, session_token):
    logger.debug("Admin session removed")
```

### M5: SSE 请求链路 — `routers/sse.py`

在现有 `_record_request` 调用旁补日志，覆盖所有错误分支：

```python
# auth_fail
logger.warning("SSE: auth failed (token missing or invalid)")

# bad_request（各种）
logger.warning("SSE: bad request — {}", error_detail)

# no_bot
logger.warning("SSE: no bot connected (token={}...)", token[:10])

# session_not_found
logger.warning("SSE: session not found {} (token={}...)", body.sessionId, token[:10])

# send_fail
logger.error("SSE: send_to_bot failed (token={}...)", token[:10])

# list_sessions / create_session auth fail
logger.warning("SSE: sessions auth failed")
```

### M6: 路由层

#### `routers/tokens.py`

```python
@router.post("/api/token")
async def create_token():
    token = await state.token_manager.generate()
    logger.info("Token created via public API: {}...", token[:10])
    return {"token": token}

@router.post("/api/token/validate")
async def validate_token(body: dict):
    token = body.get("token", "")
    valid = await state.token_manager.validate(token)
    logger.debug("Token validate: {}... valid={}", token[:10] if token else "?", valid)
    return {... }
```

#### `routers/admin.py`

```python
# _require_admin 失败时
logger.warning("Admin auth rejected: missing or invalid session cookie")
```

#### `routers/metrics.py`

```python
# DELETE auth 失败时
logger.warning("Metrics reset rejected: invalid authorization")

# DELETE 成功时
logger.info("Metrics reset by admin")
```

#### `routers/media.py`

```python
# upload 成功时（router 层面）
logger.info("Media uploaded: {} ({} bytes) token={}...", file.filename, file.size, token[:10])

# upload 异常时
logger.exception("Media upload failed: token={}...", token[:10])
```

### M7: 静默异常修复

所有 `except ...: pass` 改为至少一条日志：

```python
# session_store.py:144 — P0
except Exception:
    logger.warning("Redis cache invalidation failed during session cleanup")

# bridge.py:183 — P1
except Exception:
    logger.warning("Failed to close bot WebSocket during token removal (token={}...)", token[:10])

# bridge.py:391 — P1
except Exception:
    logger.warning("Failed to close bot WebSocket on disconnect command")

# bridge.py:419 — P2
except Exception:
    logger.debug("WebSocket close error during shutdown (ignored)")

# bridge.py:432, 440 — P2
except asyncio.CancelledError:
    logger.debug("Background task cancelled during shutdown")
```

### M8: 存储层计时

#### `infra/storage/s3.py` `put_object`

```python
async def put_object(self, key, data, content_type, ...):
    t0 = time.time()
    try:
        # ... existing S3 upload logic ...
        elapsed = time.time() - t0
        logger.info("S3 put: key={} size={} type={} took={:.1f}ms", key, len(data), content_type, elapsed * 1000)
        return url
    except Exception:
        elapsed = time.time() - t0
        logger.exception("S3 put failed: key={} took={:.1f}ms", key, elapsed * 1000)
        raise
```

#### `infra/storage/ifly_gateway.py` `put_object`

```python
async def put_object(self, key, data, content_type, ...):
    t0 = time.time()
    try:
        # ... existing upload logic ...
        elapsed = time.time() - t0
        logger.info("iFlytek put: key={} size={} took={:.1f}ms", key, len(data), elapsed * 1000)
        return url
    except Exception:
        elapsed = time.time() - t0
        logger.exception("iFlytek put failed: key={} took={:.1f}ms", key, elapsed * 1000)
        raise
```

---

## 6. 日志级别规范

统一定义各级别使用场景，避免级别混乱：

| 级别 | 用途 | 示例 |
|------|------|------|
| `DEBUG` | 高频操作、流程追踪、调试辅助 | Queue publish/consume、Token validate、Session lookup |
| `INFO` | 关键状态变更、生命周期事件、低频操作 | Bot connect/disconnect、Token created、Server start/stop、S3 upload |
| `WARNING` | 可恢复的异常、鉴权失败、数据问题 | Auth rejected、Redis cache fail、NOGROUP recovery、silent except |
| `ERROR` | 不可恢复错误、业务流程中断 | send_to_bot failed、migration failed |
| `EXCEPTION` | 同 ERROR，但自动附带 traceback | except 块中替代 error，需要 stack trace 时 |

---

## 7. 新增环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `LOG_DIR` | `server/logs` | 日志文件目录 |
| `LOG_FORMAT_JSON` | `false` | 是否输出 JSON 格式（用于日志聚合系统） |

---

## 8. 关键设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 高频操作日志级别 | DEBUG | Queue/Token validate 每秒调用多次，INFO 会淹没其他日志 |
| 鉴权失败日志级别 | WARNING | 可能是攻击探测，需要可见但不触发 ERROR 告警 |
| 存储操作计时 | INFO + 毫秒 | 外部 I/O 耗时是性能问题的首要指标 |
| JSON 格式为可选 | 环境变量开关 | 开发环境保持可读文本，生产环境按需开启 |
| 不加请求级 middleware | 不加 | 当前 uvicorn access log 已覆盖，且 OTLP metrics 已有请求指标 |
| 密码/Token 脱敏 | token[:10] + "..." | 保留可追踪前缀，不暴露完整密钥 |

---

## 9. 实现计划

| 顺序 | 模块 | 预估改动 | 优先级 |
|------|------|---------|--------|
| 1 | M1: 日志基础设施 | `infra/log.py` — error sink + JSON + 可配置路径 | P0 |
| 2 | M7: 静默异常修复 | 6 处 except 块 | P0 |
| 3 | M3: queue 核心 | `services/queue.py` 7 个方法 | P0 |
| 4 | M5: SSE 请求链路 | `routers/sse.py` 6 处错误分支 | P0 |
| 5 | M8: 存储层计时 | `s3.py` + `ifly_gateway.py` | P0 |
| 6 | M2: 启动链路 | `app.py` + `config.py` + `cache.py` + `database.py` + `storage/` | P1 |
| 7 | M4: 鉴权链路 | `token_manager.py` + `admin_auth.py` | P1 |
| 8 | M6: 路由层 | `tokens.py` + `admin.py` + `metrics.py` + `media.py` | P1 |

> P0 模块（1-5）解决"线上出问题完全看不到日志"的核心痛点。
> P1 模块（6-8）补齐完整可观测性。

---

## 10. 验证清单

- [ ] 启动日志包含完整配置摘要（Redis/MySQL/Storage/OTLP）
- [ ] 任意 `/bridge/chat` 401/400/404/500 错误在日志中可见
- [ ] Queue publish/consume 操作在 DEBUG 级别可追踪
- [ ] S3/iFlytek 上传有成功日志 + 耗时（毫秒）
- [ ] S3/iFlytek 上传失败有 exception 日志 + 耗时
- [ ] 所有 `except: pass` 块改为至少一条 warning/debug
- [ ] `LOG_FORMAT_JSON=true` 时输出合法 JSON
- [ ] `logs/error.log` 仅包含 WARNING 及以上
- [ ] 默认 INFO 级别下日志量不会显著增加（新增多为 DEBUG/WARNING）

---

确认后可使用「代码实现」进入下一阶段。
