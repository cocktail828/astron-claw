# 技术方案：会话模型简化 — 移除 active_session

## 1. 技术方案概述

移除 `chat_active_sessions` 表及其相关的 active session 概念，简化会话模型：

- 不带 `sessionId` 时直接创建新 session，不恢复上次会话
- 移除 bot 上下线对 session 的通知推送
- 移除 Redis → MySQL 的迁移兼容代码

## 2. 变更影响分析

### 2.1 删除项

| 类型 | 目标 | 说明 |
|------|------|------|
| MySQL 表 | `chat_active_sessions` | 整表删除，Alembic 迁移 |
| ORM Model | `ChatActiveSession` | 从 `models.py` 移除 |
| Redis Key | `bridge:active:{token}` | 不再读写 |
| 方法 | `SessionStore.get_active_session()` | 删除 |
| 方法 | `SessionStore.switch_session()` | 删除 |
| 方法 | `SessionStore._maybe_migrate_from_redis()` | 删除 |
| 方法 | `ConnectionBridge.get_active_session()` | 删除 |
| 方法 | `ConnectionBridge.switch_session()` | 删除 |
| 方法 | `ConnectionBridge.notify_bot_connected()` | 仅保留日志，移除 session 推送 |
| 方法 | `ConnectionBridge.notify_bot_disconnected()` | 仅保留日志，移除 session 推送 |

### 2.2 修改项

| 文件 | 修改内容 |
|------|----------|
| `session_store.py` | `create_session()` 移除 active session upsert；`get_sessions()` 返回值从 `(list, active_id)` 改为 `list`；`remove_sessions()` 移除 active 表/缓存清理；`cleanup_old_sessions()` 移除 active 指针维护；`_repopulate_cache()` 移除 active key 写入；删除 `_ACTIVE_PREFIX` 常量；删除 TTL 迁移检测逻辑 |
| `bridge.py` | `send_to_bot()` 移除 `get_active_session` fallback（session_id 改为必传）；`handle_bot_message()` 移除 3 处 `get_active_session` fallback（无 session_id 时 drop 事件）；`notify_bot_connected/disconnected()` 仅保留日志；`unregister_bot()` 移除 notify 调用；`get_sessions()` 对齐新返回值 |
| `sse.py` | `_resolve_session()` 移除恢复 active 分支（仅两条路径：带 sessionId → 校验，不带 → 创建）；`chat_sse()` 移除 `switch_session()` 调用 |
| `websocket.py` | 移除 `notify_bot_connected()` 调用 |
| `models.py` | 删除 `ChatActiveSession` 类 |
| `conftest.py` | 移除 `get_active_session`、`switch_session` mock |
| `test_bridge.py` | 移除/重写 active session 相关测试 |
| `test_sse.py` | 移除 `test_restore_active_session`、`test_active_session_stale_creates_new` 测试；移除 `switch_session` mock |
| `test_session_store.py` | 重写所有涉及 `ChatActiveSession` 的测试 |

### 2.3 不变项

| 组件 | 说明 |
|------|------|
| `chat_sessions` 表 | 保持不变 |
| `bridge:sessions:{token}` Redis 缓存 | 保持不变 |
| `bridge:chat_inbox:{token}:{session_id}` | 保持不变，基于精确 sessionId 路由 |
| `_pending_requests` | 保持不变，提供精确的 request → session 映射 |
| bot 消息中 `params.sessionId` 路由 | 保持不变 |

## 3. 关键设计决策

### 3.1 `send_to_bot()` 的 session_id 参数

**现状**：`session_id` 可选，为空时 fallback 到 `get_active_session`

**改为**：`session_id` 必传。调用方 `chat_sse()` 在调用前已通过 `_resolve_session()` 确定了 session_id，不存在无 session_id 的情况。

### 3.2 `handle_bot_message()` 无 session_id 时的处理

三处 fallback 到 `get_active_session` 的场景：

| 场景 | 现状 fallback | 改为 |
|------|-------------|------|
| 通知无 `params.sessionId` | `get_active_session` | 日志 warning + 丢弃（bot 实现应带 sessionId） |
| RPC result 无 `_pending_requests` | `get_active_session` | 仅日志（result 本身不转发给 session） |
| RPC error 无 `_pending_requests` | `get_active_session` | 日志 warning + 丢弃（跨 worker 边界情况） |

**理由**：正常流程中 session_id 通过 `params.sessionId` 或 `_pending_requests` 精确获得。去掉 fallback 后，只有异常/边界情况受影响，且这些情况下 fallback 到"上次会话"本身也不一定正确。

### 3.3 `get_sessions()` 返回值简化

**现状**：`([(session_id, number), ...], active_id)` — 调用方需解构 tuple

**改为**：`[(session_id, number), ...]` — 直接返回列表

影响调用方：`sse.py` 的 `list_sessions`、`create_session`、`_resolve_session`。

### 3.4 `notify_bot_connected/disconnected` 保留方法签名

不完全删除方法，仅移除 session 推送逻辑，保留日志输出。

**理由**：
- `websocket.py` 和 `unregister_bot` 中的调用点保持不变，避免改动扩散
- 日志记录 bot 上下线仍有运维价值
- 未来如需恢复通知功能，只需在方法内添加逻辑

## 4. 数据迁移

### 4.1 Alembic Migration

新增迁移文件：`drop_chat_active_sessions_table.py`

```python
def upgrade() -> None:
    op.drop_index('uk_chat_active_sessions_token', table_name='chat_active_sessions')
    op.drop_table('chat_active_sessions')

def downgrade() -> None:
    op.create_table(
        'chat_active_sessions',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('token', sa.String(64), nullable=False),
        sa.Column('active_session_id', sa.String(36), nullable=False),
        sa.Column('updated_at', sa.Double(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('uk_chat_active_sessions_token', 'chat_active_sessions', ['token'], unique=True)
```

### 4.2 Redis 清理

部署后可选执行，清理遗留的 `bridge:active:*` key：

```bash
redis-cli --scan --pattern 'bridge:active:*' | xargs redis-cli del
```

## 5. 实现计划

| 步骤 | 内容 | 涉及文件 |
|------|------|----------|
| 1 | 简化 `SessionStore`：移除 active session 相关逻辑、迁移代码，简化 `get_sessions` 返回值 | `session_store.py` |
| 2 | 简化 `ConnectionBridge`：移除 `get_active_session`/`switch_session`，`send_to_bot` session_id 必传，`handle_bot_message` 移除 fallback，`notify_*` 仅保留日志 | `bridge.py` |
| 3 | 简化 `sse.py`：`_resolve_session` 两分支，移除 `switch_session` 调用，对齐 `get_sessions` 返回值 | `sse.py` |
| 4 | 简化 `websocket.py`：移除 `notify_bot_connected` 调用 | `websocket.py` |
| 5 | 删除 ORM Model + 新增 Alembic 迁移 | `models.py`, `migrations/` |
| 6 | 更新测试 | `conftest.py`, `test_bridge.py`, `test_sse.py`, `test_session_store.py` |
| 7 | 运行单元测试 + E2E 验证 | — |

---

确认后可进入代码实现阶段。
