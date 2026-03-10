# 技术方案：应用启动时自动执行数据库迁移

## 1. 技术方案概述

将 `alembic upgrade head` 从 Dockerfile CMD 移到应用启动（lifespan）阶段执行，使 `uv run python run.py` 和 Docker 两种启动方式都能自动完成数据库迁移。核心挑战：

- **多 worker / 分布式部署**：多个 uvicorn worker 同时启动，只能有一个执行迁移
- **DDL 权限缺失**：数据库账户可能无 DDL 权限，需优雅降级（警告+跳过）

## 2. 技术选型

| 关注点 | 选型 | 理由 |
|--------|------|------|
| 迁移工具 | Alembic（已有） | 项目已使用，无需引入新依赖 |
| 分布式锁 | Redis `SET NX EX` | 项目已有 Redis 依赖，且已有类似用法（`bridge:cleanup_lock`） |
| Redis 模式 | 兼容 Standalone + Cluster | 项目已通过 `RedisConfig.cluster` 支持两种模式，`SET NX` 两者均支持 |

## 3. 架构设计

```
app.py lifespan
    │
    ├── init_db()          # 已有：创建引擎
    ├── init_redis()       # 已有：创建 Redis 连接
    ├── run_migrations()   # ★ 新增
    │     ├── 1. 获取 Redis 分布式锁 (migrate:lock, TTL=60s)
    │     ├── 2. 获取锁成功 → 执行 alembic upgrade head
    │     │     ├── 成功 → 释放锁，记录标记 (migrate:done:{revision})
    │     │     └── DDL 权限不足 → logger.warning，释放锁
    │     └── 3. 获取锁失败 → 等待迁移完成标记（轮询 migrate:done:*）
    ├── 初始化业务服务...   # 已有
```

## 4. 模块划分

| 模块 | 文件 | 职责 |
|------|------|------|
| 迁移执行器 | `server/infra/migration.py` (新建) | 封装 alembic 调用、DDL 权限检测、异常处理 |
| 分布式锁 | 同上 | Redis `SET NX EX` 获取/释放锁 |
| 应用入口 | `server/app.py` (修改) | lifespan 中调用迁移执行器 |
| Dockerfile | `Dockerfile` (修改) | CMD 移除 `alembic upgrade head` |

## 5. 接口设计

本需求无新 API 接口，仅涉及内部模块间调用：

```python
# server/infra/migration.py

async def run_migrations(redis: Redis | RedisCluster, db_url: str) -> None:
    """应用启动时调用，自动执行数据库迁移。

    - 通过 Redis 分布式锁确保只有一个 worker 执行
    - DDL 权限不足时 warning 并跳过
    - 其他 worker 等待迁移完成后继续启动
    """
```

## 6. 数据模型

无新表。新增 Redis Key：

| Key | 类型 | TTL | 用途 |
|-----|------|-----|------|
| `migrate:lock` | STRING | 60s | 分布式锁，确保单 worker 执行迁移 |
| `migrate:done` | STRING | 300s | 迁移完成标记，其余 worker 轮询此 key 确认迁移完毕 |

## 7. 关键设计决策

| 决策点 | 方案 | 理由 |
|--------|------|------|
| DDL 权限检测方式 | 尝试执行，捕获 `OperationalError` (1142/1044) | 比提前 `SHOW GRANTS` 解析更可靠，覆盖所有 DDL 场景 |
| 锁的 TTL | 60s | 迁移通常秒级完成，60s 留足余量，防止死锁 |
| 其他 worker 等待策略 | 轮询 `migrate:done` key，间隔 1s，最长等 60s | 简单可靠，无需 Pub/Sub |
| 无 pending 迁移时 | 不获取锁，直接跳过 | 避免每次启动都抢锁，减少 Redis 开销 |
| 锁释放 | Lua 脚本原子释放（仅释放自己持有的锁） | 防止误释放其他 worker 的锁 |

## 8. 实现计划

| 步骤 | 内容 | 文件 |
|------|------|------|
| 1 | 新建 `infra/migration.py`：实现 `run_migrations()` | 新建 |
| 2 | 修改 `app.py` lifespan：在 `init_db` + `init_redis` 之后调用 `run_migrations()` | 修改 |
| 3 | 修改 `Dockerfile` CMD：移除 `alembic upgrade head`，仅保留 `python run.py` | 修改 |
| 4 | 测试：DDL 权限正常、DDL 权限缺失、多 worker 并发启动 | 验证 |
