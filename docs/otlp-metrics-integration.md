# OTLP Metrics 集成技术方案

## 一、技术方案概述

在 Astron Claw 中集成 OpenTelemetry **Metrics**，第一阶段仅覆盖 `POST /bridge/chat`，Trace / EventLog 暂不实现，但基础设施层保留对三大信号的可扩展性。

**核心策略**：不部署外部 OTLP Collector，改用 **Redis 作为指标聚合存储**，通过 `GET /api/metrics` 暴露 **Prometheus 标准 exposition format**，Prometheus 可直接 scrape。后续部署 Collector 时只需新增一个 OTLP Exporter，业务侧零改动。

### 核心设计原则

| 原则 | 说明 |
|------|------|
| **低侵入** | 业务代码通过路由内埋点接入，不污染核心逻辑 |
| **可扩展** | infra 层提供统一 Provider，后续加 Trace/Log 只需在 Provider 里开启 |
| **可关闭** | `OTLP_ENABLED=false` 一键关闭全部遥测，零开销（OTel NoOp 模式） |
| **与现有架构一致** | 沿用 frozen dataclass 配置、`infra/` 基础设施层、`services/state` 全局单例模式 |

### 数据流

```
Worker-1 ──┐                         ┌── GET /api/metrics ──→ Prometheus Text
Worker-2 ──┼─→ RedisMetricExporter ──┼─→ Redis Hash
Worker-N ──┘    (周期 flush delta)    └── (后续) OTLP Exporter ──→ Collector
```

**多 Worker 聚合策略**：
- **Counter / Histogram**（只增不减）：各 Worker 通过 `PeriodicExportingMetricReader` 周期性将 **delta** 通过 Redis `HINCRBYFLOAT` 原子累加到共享 key，天然跨 Worker 聚合。
- **UpDownCounter → Gauge**（可增可减）：各 Worker 以 **cumulative** 时间性导出当前绝对值，写入 **per-worker key**（`{otlp}:gauges:{pid}`，带 TTL 自动过期）。Reader 在查询时 SUM 所有存活 Worker 的值。这样 Worker 崩溃后其 key 自动过期，gauge 值自愈。

---

## 二、技术选型

| 关注点 | 选型 | 理由 |
|--------|------|------|
| 指标 API | `opentelemetry-api` | 标准化插桩接口，后续切 Collector 零改动 |
| 指标 SDK | `opentelemetry-sdk` | 提供 MeterProvider、MetricReader 框架 |
| 指标存储 | **Redis**（已有） | 利用现有基础设施，原子操作天然支持多 Worker 写入 |
| 指标导出 | 自定义 `RedisMetricExporter` | 实现 `MetricExporter` 接口，将 delta 累加到 Redis |
| 查询接口 | `GET /api/metrics` | Prometheus 标准 exposition format |
| 后续扩展 | `opentelemetry-exporter-otlp-proto-grpc` | 部署 Collector 时添加，与 Redis Exporter 可并存 |

### 新增依赖（仅 2 个）

```toml
"opentelemetry-api>=1.29.0",
"opentelemetry-sdk>=1.29.0",
```

> 无需 `opentelemetry-exporter-otlp-proto-grpc`，等部署 Collector 时再添加。

---

## 三、架构设计

### 3.1 模块分层

```
┌──────────────────────────────────────────────────────────┐
│ Router 层                                                │
│  routers/sse.py        — 埋入指标记录点                    │
│  routers/metrics.py    — GET /api/metrics 查询接口         │
└───────────────────┬──────────────────────────────────────┘
                    │ 引用
┌───────────────────▼──────────────────────────────────────┐
│ Infra 层  infra/telemetry/                               │
│  ┌──────────────┐ ┌───────────────┐ ┌──────────────────┐ │
│  │ provider.py  │ │  metrics.py   │ │ redis_exporter.py│ │
│  │ 初始化/关闭   │ │ Meter + 指标  │ │ Redis 原子写入    │ │
│  └──────────────┘ └───────────────┘ └──────────────────┘ │
│  ┌──────────────┐ ┌───────────────┐                      │
│  │  config.py   │ │ reader.py     │                      │
│  │ OtlpConfig   │ │ Redis→Prom    │                      │
│  └──────────────┘ └───────────────┘                      │
└───────────────────┬──────────────────────────────────────┘
                    │ 读写
                    ▼
              Redis Keys
         {otlp}:counters          (共享 Hash, HINCRBYFLOAT)
         {otlp}:histograms        (共享 Hash, HINCRBYFLOAT)
         {otlp}:gauges:{pid}      (per-worker Hash, SET + TTL)
         {otlp}:gauge_pids        (SET, Worker PID 注册表)
         {otlp}:meta              (共享 Hash, 元数据)
         {otlp}:resource          (共享 Hash, service.name 等)
```

### 3.2 模块依赖关系

```
app.py (lifespan)
  └─→ infra/telemetry/provider.py      # init_telemetry() / shutdown_telemetry()
        ├─→ infra/telemetry/config.py   # OtlpConfig
        ├─→ infra/telemetry/metrics.py  # create_meter(), 指标注册
        └─→ infra/telemetry/redis_exporter.py  # RedisMetricExporter

routers/sse.py
  └─→ infra/telemetry/metrics.py       # 引用预定义 instrument 记录指标

routers/metrics.py
  └─→ infra/telemetry/reader.py        # 从 Redis 读取 + 格式化为 Prometheus text
```

---

## 四、Redis 数据模型

利用 Redis Hash 的 `HINCRBYFLOAT` 原子操作，多 Worker 直接累加，无锁无冲突。

> **Redis Cluster 兼容**：所有 key 使用 `{otlp}` hash tag 前缀，确保落在同一 slot。

```
# ── Resource（服务标识） ─────────────────────────────
Key:   {otlp}:resource
Field: service.name → "astron-claw"  (由 init_telemetry 写入一次)

# ── Counter ──────────────────────────────────────────
Key:   {otlp}:counters
Field: {metric_name}|{sorted_attrs_json}
Value: float (HINCRBYFLOAT 原子累加 delta)

示例:
  HINCRBYFLOAT {otlp}:counters
    "bridge.chat.requests|{\"status\":\"success\",\"token_prefix\":\"sk-abc123\"}"
    1.0

# ── Histogram ────────────────────────────────────────
Key:   {otlp}:histograms
Field: {metric_name}|{sorted_attrs_json}|count       → int  (HINCRBYFLOAT)
Field: {metric_name}|{sorted_attrs_json}|sum         → float (HINCRBYFLOAT)
Field: {metric_name}|{sorted_attrs_json}|bucket_{le} → int  (HINCRBYFLOAT)

示例:
  HINCRBYFLOAT {otlp}:histograms
    "bridge.chat.request.duration|{\"status\":\"success\"}|count"  1
  HINCRBYFLOAT {otlp}:histograms
    "bridge.chat.request.duration|{\"status\":\"success\"}|sum"    0.235
  HINCRBYFLOAT {otlp}:histograms
    "bridge.chat.request.duration|{\"status\":\"success\"}|bucket_0.1"  1

# ── Gauge（UpDownCounter）— per-worker ───────────────
Key:   {otlp}:gauges:{pid}          ← 每个 Worker 独立 key
TTL:   export_interval_ms * 3       ← Worker 崩溃后自动过期
Field: {metric_name}|{sorted_attrs_json}
Value: float (SET 绝对值，非 INCR)

# ── Gauge Worker 注册表 ─────────────────────────────
Key:   {otlp}:gauge_pids            ← SET 类型，存放所有存活 Worker 的 PID
Value: {"12345", "12346", ...}

Exporter 写入流程（pipeline 合并为一次 round-trip）:
  HSET    {otlp}:gauges:12345  field value ...   ← 写 gauge 数据
  PEXPIRE {otlp}:gauges:12345  30000              ← 刷新 TTL
  SADD    {otlp}:gauge_pids    12345              ← 注册 PID

Reader 读取流程（替代 SCAN）:
  SMEMBERS {otlp}:gauge_pids                      ← O(Worker数)，个位数
    → 对每个 pid:
      HGETALL {otlp}:gauges:{pid}                 ← key 不存在返回空（已过期）
      若为空 → SREM {otlp}:gauge_pids {pid}       ← 懒清理崩溃 Worker

# ── 元数据 ───────────────────────────────────────────
Key:   {otlp}:meta
Field: {metric_name}
Value: JSON {"type":"counter|histogram|up_down_counter", "description":"...", "unit":"..."}
```

**设计要点**：
- **Counter / Histogram**：共享 Hash + `HINCRBYFLOAT`，多 Worker 并发安全，一次 `HGETALL` 读取全部
- **Gauge**：per-worker Hash + TTL 自动过期，Worker 崩溃后 gauge 自愈（流已断开，归零正确）
- **Gauge 读取**：通过 `{otlp}:gauge_pids` Registry SET 枚举 Worker，避免 SCAN 全 keyspace（详见决策 4）
- **`{otlp}` hash tag**：Redis Cluster 下所有 key 落在同一 slot

### Worker 崩溃对指标精度的影响

| 指标类型 | 崩溃影响 | 精度 |
|----------|----------|------|
| **Gauge** (active_streams) | key TTL 过期 → 归零 | **准确** — Worker 死亡时其 SSE 流也断开，归零反映真实状态 |
| **Counter** (requests) | 丢失最近 ≤1 个 export 周期的增量（内存中未 flush 的 delta） | **微量偏低** — 上限 = export_interval（默认 10s）内的请求数 |
| **Histogram** (duration) | 同 Counter，丢失未 flush 的样本 | **微量偏低** — 可接受 |

> 这是所有周期性导出系统（Prometheus client、OTel SDK、StatsD）的固有特性。缩短 `OTLP_EXPORT_INTERVAL_MS` 可减小丢失窗口，代价是 Redis 写入频率增加。

---

## 五、接口设计

### 5.1 `GET /api/metrics` — Prometheus Exposition Format

```
GET /api/metrics
Content-Type: text/plain; version=0.0.4; charset=utf-8
```

响应示例：

```prometheus
# HELP bridge_chat_requests_total /bridge/chat 请求总数
# TYPE bridge_chat_requests_total counter
bridge_chat_requests_total{service="astron-claw",status="success",token_prefix="sk-abc123..."} 142
bridge_chat_requests_total{service="astron-claw",status="auth_fail",token_prefix=""} 3

# HELP bridge_chat_request_duration_seconds /bridge/chat 首字节耗时
# TYPE bridge_chat_request_duration_seconds histogram
bridge_chat_request_duration_seconds_bucket{service="astron-claw",status="success",token_prefix="sk-abc123...",le="0.005"} 10
bridge_chat_request_duration_seconds_bucket{service="astron-claw",status="success",token_prefix="sk-abc123...",le="0.01"} 25
bridge_chat_request_duration_seconds_bucket{service="astron-claw",status="success",token_prefix="sk-abc123...",le="0.025"} 50
bridge_chat_request_duration_seconds_bucket{service="astron-claw",status="success",token_prefix="sk-abc123...",le="0.05"} 80
bridge_chat_request_duration_seconds_bucket{service="astron-claw",status="success",token_prefix="sk-abc123...",le="0.1"} 110
bridge_chat_request_duration_seconds_bucket{service="astron-claw",status="success",token_prefix="sk-abc123...",le="0.25"} 130
bridge_chat_request_duration_seconds_bucket{service="astron-claw",status="success",token_prefix="sk-abc123...",le="0.5"} 138
bridge_chat_request_duration_seconds_bucket{service="astron-claw",status="success",token_prefix="sk-abc123...",le="1.0"} 141
bridge_chat_request_duration_seconds_bucket{service="astron-claw",status="success",token_prefix="sk-abc123...",le="2.5"} 142
bridge_chat_request_duration_seconds_bucket{service="astron-claw",status="success",token_prefix="sk-abc123...",le="5.0"} 142
bridge_chat_request_duration_seconds_bucket{service="astron-claw",status="success",token_prefix="sk-abc123...",le="10.0"} 142
bridge_chat_request_duration_seconds_bucket{service="astron-claw",status="success",token_prefix="sk-abc123...",le="+Inf"} 142
bridge_chat_request_duration_seconds_sum{service="astron-claw",status="success",token_prefix="sk-abc123..."} 28.5
bridge_chat_request_duration_seconds_count{service="astron-claw",status="success",token_prefix="sk-abc123..."} 142

# HELP bridge_chat_active_streams 当前活跃 SSE 流数量
# TYPE bridge_chat_active_streams gauge
bridge_chat_active_streams{service="astron-claw",token_prefix="sk-abc123..."} 3

# HELP bridge_chat_stream_duration_seconds SSE 流持续时长
# TYPE bridge_chat_stream_duration_seconds histogram
bridge_chat_stream_duration_seconds_bucket{service="astron-claw",close_reason="done",token_prefix="sk-abc123...",le="1.0"} 5
bridge_chat_stream_duration_seconds_bucket{service="astron-claw",close_reason="done",token_prefix="sk-abc123...",le="5.0"} 30
bridge_chat_stream_duration_seconds_bucket{service="astron-claw",close_reason="done",token_prefix="sk-abc123...",le="10.0"} 60
bridge_chat_stream_duration_seconds_bucket{service="astron-claw",close_reason="done",token_prefix="sk-abc123...",le="30.0"} 85
bridge_chat_stream_duration_seconds_bucket{service="astron-claw",close_reason="done",token_prefix="sk-abc123...",le="60.0"} 95
bridge_chat_stream_duration_seconds_bucket{service="astron-claw",close_reason="done",token_prefix="sk-abc123...",le="300.0"} 100
bridge_chat_stream_duration_seconds_bucket{service="astron-claw",close_reason="done",token_prefix="sk-abc123...",le="+Inf"} 100
bridge_chat_stream_duration_seconds_sum{service="astron-claw",close_reason="done",token_prefix="sk-abc123..."} 1250.0
bridge_chat_stream_duration_seconds_count{service="astron-claw",close_reason="done",token_prefix="sk-abc123..."} 100
```

#### 命名转换规则

OTel 插桩使用点分命名，输出 Prometheus 格式时自动转换：

| OTel 指标名 | Prometheus 指标名 | 规则 |
|-------------|-------------------|------|
| `bridge.chat.requests` | `bridge_chat_requests_total` | `.` → `_`，Counter 追加 `_total` |
| `bridge.chat.request.duration` | `bridge_chat_request_duration_seconds` | 追加 `_seconds` 单位后缀 |
| `bridge.chat.active_streams` | `bridge_chat_active_streams` | UpDownCounter → `gauge`，无后缀 |
| `bridge.chat.stream.duration` | `bridge_chat_stream_duration_seconds` | 追加 `_seconds` |

#### reader 模块职责

`infra/telemetry/reader.py` 从 Redis 读取原始数据后负责：

1. `.` → `_` 名称转换
2. 追加类型后缀（`_total`, `_seconds`）
3. 生成 `# HELP` / `# TYPE` 行（从 `{otlp}:meta` 读取描述和类型）
4. **注入 `service` 标签**：从 `{otlp}:resource` 读取 `service.name`，作为所有指标的首个 label
5. Histogram 展开：`_bucket{le="..."}`, `_sum`, `_count`（bucket 需从独立计数转为 Prometheus 累积计数）
6. Gauge 聚合：`SMEMBERS {otlp}:gauge_pids` → `HGETALL` 每个 Worker → SUM 同名 field，懒清理过期 PID
7. Label 值转义：`\`, `"`, `\n` 按 Prometheus 规范转义
8. 返回 `text/plain; version=0.0.4; charset=utf-8`

### 5.2 `DELETE /api/metrics` — 指标重置（JSON）

```
DELETE /api/metrics
Authorization: Bearer <admin-token>
```

```json
{"ok": true, "message": "All metrics reset"}
```

---

## 六、指标定义

### `/bridge/chat` 第一阶段指标

| 指标名 (OTel) | 类型 | 单位 | 描述 | 属性 |
|----------------|------|------|------|------|
| `bridge.chat.requests` | Counter | - | 请求总数 | `service`, `status`, `token_prefix` |
| `bridge.chat.request.duration` | Histogram | s | 首字节耗时 | `service`, `status`, `token_prefix` |
| `bridge.chat.stream.duration` | Histogram | s | SSE 流持续时长 | `service`, `close_reason`, `token_prefix` |
| `bridge.chat.active_streams` | UpDownCounter | - | 活跃 SSE 流数量 | `service`, `token_prefix` |

> **`service` 标签来源**：由 reader 在 Prometheus 渲染时从 `{otlp}:resource` 中的 `service.name` 统一注入，业务埋点代码无需手动传入。

**`status` 枚举值**：
- `success` — 请求成功进入 SSE 流
- `auth_fail` — 认证失败
- `bad_request` — 请求参数校验失败
- `no_bot` — 无 Bot 连接
- `session_not_found` — Session 不存在
- `send_fail` — 消息发送到 Bot 失败
- `error` — 内部错误

**`close_reason` 枚举值**：
- `done` — 正常完成
- `error` — 流内错误
- `timeout` — 超时关闭
- `client_disconnect` — 客户端断开

**Histogram bucket 定义**：
- `request.duration`: `[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]`
- `stream.duration`: `[1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0]`

### Metric 属性规范

| 属性键 | 类型 | 来源 | 说明 | 示例 |
|--------|------|------|------|------|
| `service` | string | Resource 注入 | 服务名称，reader 渲染时从 `{otlp}:resource` 自动注入 | `"astron-claw"` |
| `status` | string | 业务埋点 | 业务结果 | `"success"` |
| `token_prefix` | string | 业务埋点 | token 前 10 字符脱敏 | `"sk-abc123..."` |
| `close_reason` | string | 业务埋点 | SSE 流关闭原因 | `"done"` |

---

## 七、内部模块接口

### 7.1 `infra/telemetry/config.py`

```python
@dataclass(frozen=True)
class OtlpConfig:
    enabled: bool            # 总开关
    service_name: str        # 服务标识
    export_interval_ms: int  # Metric flush 到 Redis 的周期 (ms)
    metrics_enabled: bool    # Metric 信号子开关
    traces_enabled: bool     # Trace 信号子开关（第一阶段 False）
    logs_enabled: bool       # EventLog 信号子开关（第一阶段 False）
```

### 7.2 `infra/telemetry/provider.py`

```python
async def init_telemetry(config: OtlpConfig, redis: Redis) -> None:
    """初始化 OTel MeterProvider + RedisMetricExporter。
    若 config.enabled=False 则不做任何操作（OTel API 自动 NoOp）。
    """

async def shutdown_telemetry() -> None:
    """优雅关闭所有 Provider，确保缓冲指标被 flush 到 Redis。"""
```

### 7.3 `infra/telemetry/metrics.py`

```python
def get_meter(name: str = "astron_claw") -> Meter:
    """获取 Meter 实例（若 OTLP 未启用返回 NoOp Meter）。"""

# 预定义指标 instrument 对象
chat_request_total: Counter          # bridge.chat.requests
chat_request_duration: Histogram     # bridge.chat.request.duration
chat_stream_duration: Histogram      # bridge.chat.stream.duration
chat_active_streams: UpDownCounter   # bridge.chat.active_streams
```

### 7.4 `infra/telemetry/redis_exporter.py`

```python
class RedisMetricExporter(MetricExporter):
    """将 OTel metrics 写入 Redis。
    - Counter / Histogram: delta 时间性，HINCRBYFLOAT 到共享 Hash
    - UpDownCounter (Gauge): cumulative 时间性，SET 到 per-worker Hash + TTL
    """

    def export(self, metrics_data, ...) -> MetricExportResult: ...
    def shutdown(self, ...) -> None: ...
    def force_flush(self, ...) -> bool: ...
```

### 7.5 `infra/telemetry/reader.py`

```python
async def render_prometheus_exposition(redis: Redis) -> str:
    """从 Redis 读取全部指标数据，注入 service 标签，格式化为 Prometheus exposition text。

    - Counter / Histogram: 从 {otlp}:counters / {otlp}:histograms 读取
    - Gauge: SMEMBERS {otlp}:gauge_pids → HGETALL 每个 Worker → SUM，懒 SREM 过期 PID
    - service 标签: 从 {otlp}:resource 的 service.name 读取，注入到所有指标
    """
```

---

## 八、关键设计决策

### 决策 1：Redis 聚合 vs 内存存储

| 方案 | 优点 | 缺点 |
|------|------|------|
| In-Memory | 最简单 | 多 Worker 各自独立，查询只能看到单 Worker 数据 |
| **Redis 原子聚合** | 多 Worker 自动聚合，单一查询入口 | 需自定义 Exporter |

**选择 Redis**。项目使用多 Worker 部署且已有 Redis，`HINCRBYFLOAT` 天然支持并发写入聚合。

### 决策 2：Delta 导出 + Redis 原子累加（Counter / Histogram）

OTel SDK 的 `PeriodicExportingMetricReader` 支持 delta 时间性（每次导出增量）。`RedisMetricExporter` 接收 delta 后通过 `HINCRBYFLOAT` 原子累加到 Redis，各 Worker 的增量自动合并为全局累计值。

### 决策 3：Cumulative 导出 + per-worker 覆写（UpDownCounter / Gauge）

UpDownCounter 可增可减，若用 delta + `HINCRBYFLOAT`，Worker 崩溃会导致 gauge 永久漂移（未发出 -1 的增量丢失）。因此 gauge 采用 **cumulative 时间性**导出绝对值，每个 Worker 写自己的 key（`{otlp}:gauges:{pid}`，带 TTL）。Reader 查询时 SUM 所有存活 Worker 的值。Worker 崩溃 → key 过期 → gauge 自愈。

### 决策 4：Gauge 读取 — Registry SET 替代 SCAN

**问题**：`SCAN {otlp}:gauges:*` 的复杂度是 O(N)，N = Redis 实例中的**总 key 数量**（非匹配数量）。即使只有 5 个 gauge key，Redis 也必须遍历整个 keyspace 逐个做 pattern 匹配。

万级并发下 Redis key 规模估算：

| Key 模式 | 数量级 |
|----------|--------|
| `bridge:chat_inbox:{token}:{session}` | 数千~万级（并发 SSE 流） |
| `bridge:bot_inbox:{token}` | 数十 |
| `bridge:sessions:{token}` | 数十 |
| `{otlp}:gauges:{pid}` | 个位数（Worker 数） |

Prometheus 每 15-30s scrape 一次，每次 SCAN 全量遍历万级 keyspace 来找个位数的 key，浪费且与业务写入竞争 CPU。

**方案**：用 `{otlp}:gauge_pids` SET 注册存活 Worker PID，读取时 `SMEMBERS`（O(Worker数)）替代 SCAN（O(总key数)）。

| | SCAN | Registry SET |
|---|------|-------------|
| 读取复杂度 | O(总 key 数) 万级 | O(Worker 数) 个位数 |
| 阻塞影响 | 每轮迭代短暂阻塞 | 不阻塞 |
| 额外写入成本 | 无 | 每次 export 多一次 SADD（pipeline 合并） |
| 崩溃清理 | key TTL 过期后自然不返回 | key TTL 过期 + Reader 懒 SREM |

### 决策 5：路由内直接埋点

SSE 长连接场景下 Middleware 无法感知流生命周期（持续时长、关闭原因），必须在流生成器内记录。

### 决策 6：NoOp 零开销

`OTLP_ENABLED=false` 时不注册 MeterProvider，OTel API 自动降级为 NoOp，业务代码无需 if/else。

### 决策 7：service 标签 — Resource 级注入

`service` 标签不由业务埋点传入（避免每处手动写 `service=xxx`），而是存储在 `{otlp}:resource` 中，由 reader 在 Prometheus 渲染时统一注入到所有指标的 label 中。这与 OTel Resource 属性的语义一致：service.name 是 Resource 级属性，不是 data-point 级属性。

### 决策 8：Prometheus exposition format

直接输出 Prometheus 标准文本格式，Prometheus 可零配置 scrape，无需中间 Collector。

### 决策 9：后续迁移路径

部署 OTLP Collector 后，只需在 provider.py 中新增一个 Reader：

```python
# provider.py — 新增 OTLP Exporter，Redis Exporter 可保留或移除
from opentelemetry.exporter.otlp.proto.grpc import OTLPMetricExporter

readers = [
    PeriodicExportingMetricReader(RedisMetricExporter(redis)),      # 保留
    PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=...)),  # 新增
]
provider = MeterProvider(metric_readers=readers)
```

业务代码（sse.py 中的埋点）完全不变。

---

## 九、文件清单

### 新增文件（7 个）

| 文件路径 | 职责 |
|----------|------|
| `server/infra/telemetry/__init__.py` | 包导出 `init_telemetry`, `shutdown_telemetry` |
| `server/infra/telemetry/config.py` | `OtlpConfig` 冻结数据类 |
| `server/infra/telemetry/provider.py` | MeterProvider 初始化与关闭 |
| `server/infra/telemetry/metrics.py` | 指标定义 + 记录辅助函数 |
| `server/infra/telemetry/redis_exporter.py` | `RedisMetricExporter` — Counter/Histogram delta→Redis, Gauge cumulative→per-worker |
| `server/infra/telemetry/reader.py` | Redis→Prometheus text 渲染，注入 service 标签，Gauge 多 Worker 聚合 |
| `server/routers/metrics.py` | `GET /api/metrics` (Prometheus) + `DELETE /api/metrics` |

### 修改文件（5 个）

| 文件路径 | 修改内容 |
|----------|----------|
| `server/pyproject.toml` | 添加 `opentelemetry-api`, `opentelemetry-sdk` |
| `server/infra/config.py` | `AppConfig` 新增 `otlp: OtlpConfig` 字段 |
| `server/app.py` | lifespan 中调用 `init_telemetry()` / `shutdown_telemetry()` + 注册 metrics 路由 |
| `server/routers/sse.py` | `chat_sse()` 和 `_stream_with_cleanup()` 中埋入指标记录 |
| `server/.env.example` | 添加 OTLP 环境变量示例 |

---

## 十、实现计划

| 步骤 | 任务 | 文件 |
|------|------|------|
| 1 | 添加 opentelemetry 依赖 | `pyproject.toml` |
| 2 | 新增 `OtlpConfig` + 集成到 `AppConfig` | `infra/telemetry/config.py`, `infra/config.py` |
| 3 | 实现 `RedisMetricExporter` | `infra/telemetry/redis_exporter.py` |
| 4 | 实现 `provider.py` — MeterProvider 初始化 | `infra/telemetry/provider.py` |
| 5 | 定义指标 instruments + 记录辅助函数 | `infra/telemetry/metrics.py` |
| 6 | 实现 Prometheus reader + metrics 路由 | `infra/telemetry/reader.py`, `routers/metrics.py` |
| 7 | 在 `app.py` lifespan 中集成初始化/关闭 | `app.py` |
| 8 | 在 `routers/sse.py` 中埋入指标记录点 | `routers/sse.py` |
| 9 | 更新 `.env.example` | `.env.example` |
| 10 | 测试验证 | - |

### 配置环境变量

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `OTLP_ENABLED` | `false` | 总开关 |
| `OTLP_SERVICE_NAME` | `astron-claw` | 服务标识 |
| `OTLP_EXPORT_INTERVAL_MS` | `10000` | 指标 flush 到 Redis 的周期 (ms) |

> `OTLP_ENDPOINT` 等 Collector 相关配置等部署 Collector 时再添加。
