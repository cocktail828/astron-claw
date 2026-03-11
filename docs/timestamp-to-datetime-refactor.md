# 时间戳字段迁移至 DateTime 类型技术方案

## 1. 技术方案概述

### 背景

当前数据库中所有时间相关字段均使用 `Double` 类型存储 Unix 时间戳（秒），在数据库直查时显示为浮点数（如 `1709280000.0`），不便于人工阅读和调试。

### 现状

| 表 | 字段 | 当前类型 | 说明 |
|----|------|---------|------|
| `tokens` | `created_at` | `Double` (float) | 创建时间 |
| `tokens` | `expires_at` | `Double` (float) | 过期时间，`9999999999.0` 表示永不过期 |
| `chat_sessions` | `created_at` | `Double` (float) | 创建时间 |

### 目标

| 目标 | 衡量标准 |
|------|---------|
| 数据库可读性 | 时间字段在 MySQL 客户端直接显示为 `2026-03-11 10:30:00` 格式 |
| 类型安全 | Python 代码使用 `datetime` 对象而非 `float` |
| 向后兼容 | API 响应中 `created_at` / `expires_at` 仍返回 Unix 时间戳（秒），前端无需改动 |
| 数据无损 | 现有数据通过 Alembic 迁移转换，精度保持到秒级 |

### 约束

- 不引入新依赖
- 不改变 API 响应格式（前端不做改动）
- `expires_at` 的"永不过期"语义需保留
- Alembic 迁移须可逆（支持 downgrade）
- Redis ZSET 时间戳（bot liveness）不在此次改动范围内（运行时数据，非持久化）

---

## 2. 技术选型

| 项目 | 选型 | 理由 |
|------|------|------|
| DB 列类型 | `DateTime` | MySQL 原生日期类型，直查可读，支持时间函数 |
| Python 类型 | `datetime.datetime` | SQLAlchemy `DateTime` 自动映射为 Python `datetime` |
| 永不过期表示 | `datetime(9999, 12, 31, 23, 59, 59)` | MySQL `DATETIME` 最大值为 `9999-12-31 23:59:59`，语义清晰 |
| 迁移工具 | Alembic `op.alter_column` + 数据转换 | 在线 ALTER + UPDATE 分离，支持 downgrade |

---

## 3. 详细设计

### 3.1 模型变更 — `infra/models.py`

```python
from datetime import datetime
from sqlalchemy import DateTime

# Token 表
created_at: Mapped[datetime] = mapped_column(
    DateTime, nullable=False,
    comment="创建时间",
)
expires_at: Mapped[datetime] = mapped_column(
    DateTime, nullable=False,
    comment="过期时间，9999-12-31 23:59:59 表示永不过期",
)

# ChatSession 表
created_at: Mapped[datetime] = mapped_column(
    DateTime, nullable=False,
    comment="创建时间",
)
```

### 3.2 "永不过期"常量变更

```python
# 改造前（token_manager.py）
_NEVER_EXPIRES = 9999999999.0

# 改造后
_NEVER_EXPIRES = datetime(9999, 12, 31, 23, 59, 59)
```

### 3.3 业务代码适配

#### token_manager.py — 写入

```python
# 改造前
now = time.time()
expires_at = _NEVER_EXPIRES if expires_in == 0 else now + expires_in

# 改造后
from datetime import datetime, timedelta, timezone

now = datetime.now(timezone.utc)
expires_at = _NEVER_EXPIRES if expires_in == 0 else now + timedelta(seconds=expires_in)
```

#### token_manager.py — 查询比较

```python
# 改造前
Token.expires_at >= time.time()

# 改造后
from datetime import datetime, timezone

Token.expires_at >= datetime.now(timezone.utc)
```

#### token_manager.py — API 响应输出

```python
# 改造前
"created_at": row.created_at,    # float
"expires_at": row.expires_at,    # float

# 改造后
"created_at": row.created_at.timestamp(),    # float (保持 API 兼容)
"expires_at": row.expires_at.timestamp(),    # float (保持 API 兼容)
```

#### session_store.py — 写入

```python
# 改造前
now = time.time()
ChatSession(... created_at=now ...)

# 改造后
from datetime import datetime, timezone

now = datetime.now(timezone.utc)
ChatSession(... created_at=now ...)
```

#### session_store.py — 清理比较

```python
# 改造前
cutoff = time.time() - max_age_seconds

# 改造后
from datetime import datetime, timedelta, timezone

cutoff = datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)
```

### 3.4 Alembic 迁移

迁移需要两步：先转换数据，再变更列类型。

**Upgrade（float → DateTime）：**

```python
def upgrade():
    # Step 1: 新增临时 DateTime 列
    op.add_column('tokens', sa.Column('created_at_new', sa.DateTime, nullable=True))
    op.add_column('tokens', sa.Column('expires_at_new', sa.DateTime, nullable=True))
    op.add_column('chat_sessions', sa.Column('created_at_new', sa.DateTime, nullable=True))

    # Step 2: 数据转换 — 使用 MySQL FROM_UNIXTIME()
    op.execute("UPDATE tokens SET created_at_new = FROM_UNIXTIME(created_at)")
    op.execute("""
        UPDATE tokens SET expires_at_new = CASE
            WHEN expires_at >= 9999999999 THEN '9999-12-31 23:59:59'
            ELSE FROM_UNIXTIME(expires_at)
        END
    """)
    op.execute("UPDATE chat_sessions SET created_at_new = FROM_UNIXTIME(created_at)")

    # Step 3: 删旧列、改新列名
    op.drop_column('tokens', 'created_at')
    op.alter_column('tokens', 'created_at_new', new_column_name='created_at', nullable=False)
    op.drop_column('tokens', 'expires_at')
    op.alter_column('tokens', 'expires_at_new', new_column_name='expires_at', nullable=False)
    op.drop_column('chat_sessions', 'created_at')
    op.alter_column('chat_sessions', 'created_at_new', new_column_name='created_at', nullable=False)

    # Step 4: 重建索引
    op.create_index('idx_tokens_expires_at', 'tokens', ['expires_at'])
    op.create_index('idx_chat_sessions_created_at', 'chat_sessions', ['created_at'])
```

**Downgrade（DateTime → float）：**

```python
def downgrade():
    # 逆向操作：DateTime → UNIX_TIMESTAMP() → Double
    op.add_column('tokens', sa.Column('created_at_old', sa.Double, nullable=True))
    op.add_column('tokens', sa.Column('expires_at_old', sa.Double, nullable=True))
    op.add_column('chat_sessions', sa.Column('created_at_old', sa.Double, nullable=True))

    op.execute("UPDATE tokens SET created_at_old = UNIX_TIMESTAMP(created_at)")
    op.execute("""
        UPDATE tokens SET expires_at_old = CASE
            WHEN expires_at = '9999-12-31 23:59:59' THEN 9999999999.0
            ELSE UNIX_TIMESTAMP(expires_at)
        END
    """)
    op.execute("UPDATE chat_sessions SET created_at_old = UNIX_TIMESTAMP(created_at)")

    op.drop_column('tokens', 'created_at')
    op.alter_column('tokens', 'created_at_old', new_column_name='created_at', nullable=False)
    op.drop_column('tokens', 'expires_at')
    op.alter_column('tokens', 'expires_at_old', new_column_name='expires_at', nullable=False)
    op.drop_column('chat_sessions', 'created_at')
    op.alter_column('chat_sessions', 'created_at_old', new_column_name='created_at', nullable=False)

    op.create_index('idx_tokens_expires_at', 'tokens', ['expires_at'])
    op.create_index('idx_chat_sessions_created_at', 'chat_sessions', ['created_at'])
```

### 3.5 测试适配

`test_token_manager.py` 中需将 `9999999999.0` 替换为 `datetime(9999, 12, 31, 23, 59, 59)`:

```python
# 改造前
assert token_obj.expires_at == 9999999999.0

# 改造后
from datetime import datetime
assert token_obj.expires_at == datetime(9999, 12, 31, 23, 59, 59)
```

---

## 4. 改动范围

### 不改动的部分

| 类别 | 说明 |
|------|------|
| Redis ZSET 时间戳 | `bridge:bot_alive` 的 score 仍用 `time.time()`，是运行时数据非持久化 |
| SSE/Metrics 计时 | `sse.py` 中的 deadline/heartbeat/duration 是运行时计算，不涉及数据库 |
| 存储层计时 | `s3.py`/`ifly_gateway.py` 的上传耗时计算 |
| API 响应格式 | `created_at`/`expires_at` 仍以 Unix 时间戳返回，前端零改动 |
| 前端 | `admin.html` / `index.html` 不受影响 |

### 需改动的部分

| 模块 | 文件 | 改动类型 |
|------|------|---------|
| **M1: 数据模型** | `infra/models.py` | 3 个字段 `Double` → `DateTime`，类型注解 `float` → `datetime` |
| **M2: Alembic 迁移** | `migrations/versions/xxx_timestamp_to_datetime.py` | 新建迁移脚本 |
| **M3: Token 管理** | `services/token_manager.py` | `time.time()` → `datetime.now(timezone.utc)`，`_NEVER_EXPIRES` 常量，API 输出 `.timestamp()` |
| **M4: Session 存储** | `services/session_store.py` | `time.time()` → `datetime.now(timezone.utc)`，cutoff 比较改为 `timedelta` |
| **M5: 测试适配** | `tests/test_token_manager.py` | `9999999999.0` → `datetime(9999, 12, 31, 23, 59, 59)` |

---

## 5. 关键设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 时区处理 | UTC (`timezone.utc`) | 服务端统一 UTC，避免时区混乱；MySQL `DATETIME` 存储不含时区信息，约定为 UTC |
| 永不过期值 | `datetime(9999, 12, 31, 23, 59, 59)` | MySQL `DATETIME` 最大合法值，与原 `9999999999.0`（约 2286 年）语义对齐 |
| API 兼容 | `.timestamp()` 输出 | 保持 API 返回 float 类型的 Unix 时间戳，前端零改动 |
| 迁移策略 | 新增临时列 + 数据转换 + 改名 | 避免直接 ALTER COLUMN 导致数据丢失，支持 downgrade |
| 精度 | 秒级 | 原 `time.time()` 虽有微秒精度，但业务场景秒级足够；`DATETIME` 也支持到秒 |

---

## 6. 实现计划

| 顺序 | 模块 | 预估改动 | 说明 |
|------|------|---------|------|
| 1 | M1: 数据模型 | `infra/models.py` ~3 处 | 基础设施，其他模块依赖 |
| 2 | M2: Alembic 迁移 | 新建迁移脚本 | 数据库 schema 变更 + 数据转换 |
| 3 | M3: Token 管理 | `services/token_manager.py` ~8 处 | 写入/查询/比较/输出 |
| 4 | M4: Session 存储 | `services/session_store.py` ~3 处 | 写入/清理比较 |
| 5 | M5: 测试适配 | `tests/test_token_manager.py` ~3 处 | 常量值更新 |

---

## 7. 验证清单

- [ ] `infra/models.py` 中 3 个时间字段均为 `DateTime` 类型
- [ ] Alembic 迁移可正向执行（upgrade），现有数据正确转换
- [ ] Alembic 迁移可逆向执行（downgrade），数据还原无损
- [ ] `token_manager.py` 全部 `time.time()` 替换为 `datetime.now(timezone.utc)`
- [ ] `session_store.py` 全部 `time.time()` 替换为 `datetime.now(timezone.utc)`
- [ ] API 响应中 `created_at`/`expires_at` 仍为 Unix 时间戳（float）
- [ ] "永不过期" Token 的 `expires_at` 在 DB 中显示为 `9999-12-31 23:59:59`
- [ ] 现有测试全部通过
- [ ] MySQL 直查时间字段显示人类可读日期

---

确认后可使用「代码实现」进入下一阶段。
