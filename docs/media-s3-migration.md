# 技术方案：媒体存储迁移到 S3 对象存储

## 1. 技术方案概述

将 **Bridge 服务端**的媒体文件存储后端从**本地文件系统 + MySQL**迁移到 **S3 对象存储**（MinIO）。Bridge 是 astron-claw 的「渠道平台」，S3 是其存储后端。上传走 Bridge REST API（鉴权 + 校验），**下载直连 S3 公开 URL**（无鉴权、无过期、无中转）。

### 1.1 架构定位

```
钉钉:  客户端 ↔ 钉钉服务器（平台存储）   ↔ Bot 插件
飞书:  客户端 ↔ 飞书服务器（平台存储）   ↔ Bot 插件
我们:  前端   ↔ Bridge 服务器（S3 存储） ↔ Bot 插件
```

Bridge 等同于钉钉/飞书的「平台服务器」，S3 是 Bridge 的内部存储后端。

### 1.2 当前问题

| 问题 | 影响 |
|------|------|
| 本地文件系统不支持多 worker/多机共享 | 文件写入 worker-A 磁盘，worker-B 无法下载 |
| 下载走服务端代理 | `FileResponse` 完整读文件 + 写 HTTP，消耗带宽/内存 |
| MySQL 存元数据、磁盘存文件 | 两套存储需分别维护，存在不一致风险 |
| 清理依赖应用层定时任务 | `cleanup_expired()` 遍历 DB + 逐文件 `unlink` |
| Bot 入站存储路径不规范 | `inbound.ts` 裸 `writeFileSync` 到 `/tmp/`，绕过 OpenClaw SDK 媒体管理，无自动清理 |
| Bot 入站多媒体丢失 | 只处理 `mediaItems[0]`，多文件消息丢数据 |
| Bot 入站占位符语义丢失 | 统一写死 `"[Image]"`，AI 无法区分图片/文件/音视频 |

### 1.3 目标

- **Bridge 存储**：本地文件系统 + MySQL → S3（多 worker 共享）
- **上传接口**：保持 `POST /api/media/upload` 形式（multipart），内部改写为 S3 PutObject
- **下载**：返回 S3 公开直链，无鉴权、无过期、无 302 中转。删除 `/api/media/download` 端点
- **元数据**：删除 MySQL `media` 表，S3 对象自身属性即元数据
- **S3 Key**：`{session_id}/{filename}`
- **过期清理**：S3 Lifecycle Rule 自动删除，移除应用层清理代码
- **Bot 入站对齐**：用 `loadWebMedia(downloadUrl)` + `saveMediaBuffer` 替代裸 `writeFileSync`，复用 SDK 媒体管理

---

## 2. 媒体流向设计

### 2.1 总体架构

上传走 Bridge（鉴权 + 校验 + 存 S3），下载直连 S3（公开 URL）：

```
┌─────────────────────────────────────────────────────────────────────┐
│                        媒体流向总览                                   │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  上传（需鉴权）：                                                     │
│  前端/Bot → POST /api/media/upload (Bearer token)                   │
│           → Bridge 校验 → S3 PutObject                              │
│           → 返回 downloadUrl（S3 公开直链）                           │
│                                                                     │
│  下载（无需鉴权）：                                                   │
│  任何客户端 → GET downloadUrl → S3 直连下载                          │
│  无 302、无 token、无过期                                            │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 与钉钉/飞书的对比

三者本质一致：客户端上传到平台服务器，拿回引用标识符，发消息时用标识符引用。

| | 平台服务器 | 上传接口 | 引用标识 | 下载方式 |
|--|----------|---------|---------|---------|
| 钉钉 | 钉钉服务器 | oapi/media/upload | media_id / url | 下载码换临时 URL |
| 飞书 | 飞书服务器 | im.image.create / im.file.create | image_key / file_key | SDK 接口下载 |
| 我们 | Bridge（S3） | /api/media/upload | downloadUrl | S3 公开直链 |

### 2.3 入站流程（用户 → AI）

```
用户在前端发送文件 + 消息
    ↓
前端 POST /api/media/upload → Bridge → S3 PutObject
    ↓ 返回 downloadUrl（S3 公开直链，完整 URL）
前端发送聊天消息（文本 + downloadUrl 引用）→ SSE 端点
    ↓
Bridge 构造 JSON-RPC session/prompt（含 downloadUrl）→ Bot 插件
    ↓
Bot loadWebMedia(downloadUrl) → 直连 S3 下载 → Buffer
    ↓  ← downloadUrl 是公开 URL，loadWebMedia 无需鉴权即可加载
saveMediaBuffer(buffer, { contentType, fileName })   ← OpenClaw SDK 标准 API
    → 本地文件（受 localRoots 管理，自动清理）
    ↓
注入 bot 上下文：
  MediaPath  = /path/managed/by/sdk/{filename}
  MediaPaths = [path1, path2, ...]     ← 支持多媒体
  MediaType  = image/png
  占位符     = <media:image> / <media:file name="report.pdf"> 等
    ↓
OpenClaw AI 引擎处理（AI 能感知文件类型和名称）
```

**关键设计点**：

1. **`loadWebMedia(downloadUrl)` 可以直接使用**：downloadUrl 是 S3 公开直链，无需鉴权，`loadWebMedia` 作为纯 URL 加载器直接可用。无需像之前设计的那样在 `downloadMediaFromBridge` 中带 token 请求。**注意**：`loadWebMedia` 对本地路径有 `localRoots` 白名单限制，HTTP URL 不受影响。实现时在调用前加 `downloadUrl.startsWith('http')` 校验，若不满足则 log 明确错误而非让 SDK 抛 `LocalMediaAccessError`。

2. **`saveMediaBuffer` 替代 `writeFileSync`**：用 OpenClaw plugin-sdk 的 `saveMediaBuffer`（钉钉/飞书用的同一个 API），替代当前裸 `writeFileSync` 到 `/tmp/`，受 SDK `localRoots` 管理，有自动清理。

3. **支持多媒体**：遍历所有 mediaItems（不再只取第一个），注入 `MediaPaths` 数组。

4. **语义占位符**：按媒体类型注入 `<media:image>` / `<media:file name="xxx">` / `<media:audio>` 等（替代写死的 `"[Image]"`），AI 能感知文件类型和名称。

### 2.4 出站流程（AI → 用户）

```
OpenClaw AI 引擎输出 mediaUrl（本地路径或 HTTP URL）
    ↓
Bot loadWebMedia(mediaUrl) → Buffer
    ↓
Bot uploadMediaToBridge(buffer, sessionId) → POST /api/media/upload → Bridge → S3
    ↓ 返回 { downloadUrl, fileName, mimeType, fileSize, sessionId }
Bot 发送 JSON-RPC session/update（agent_media）
  payload: { downloadUrl, fileName, mimeType, fileSize }
    ↓
Bridge → 前端渲染
前端通过 downloadUrl 直连 S3 下载显示（<img src="downloadUrl">）
```

与钉钉/飞书模式对齐：Bot `loadWebMedia` → Buffer → 上传到「平台」→ 得到引用标识 → 发消息引用。

---

## 3. 技术选型

| 层面 | 选型 | 理由 |
|------|------|------|
| 对象存储 | MinIO（S3 兼容） | 本地已部署，S3 API 完全兼容，未来可迁移到 AWS S3 / 阿里云 OSS |
| Python SDK | `aiobotocore` | 原生 asyncio，与 FastAPI 事件循环契合 |
| 存储桶 | 单桶 `astron-claw-media`，**公开读** | Bucket Policy 设置 `s3:GetObject` 公开 |
| 对象 Key | `{session_id}/{filename}` | 按会话隔离，session_id 为 UUID 全局唯一 |
| 过期清理 | S3 Lifecycle Rule（`Expiration: Days=7`） | S3 自动删除，无需应用层定时任务 |
| 下载方式 | S3 公开直链 | 无鉴权、无过期、无中转，前端/Bot 直连 |

---

## 4. S3 Key 与 URL 映射

### 4.1 核心映射关系

```
S3 Key       = {session_id}/{filename}
downloadUrl  = {S3_PUBLIC_ENDPOINT}/{S3_BUCKET}/{session_id}/{filename}
             = http://192.168.1.x:9000/astron-claw-media/{session_id}/{filename}
```

**S3_ENDPOINT vs S3_PUBLIC_ENDPOINT**：Bridge 通过 `S3_ENDPOINT`（内部地址）执行 PutObject，`downloadUrl` 用 `S3_PUBLIC_ENDPOINT`（客户端可达地址）拼接。开发环境两者可以相同（都是 `localhost:9000`），生产环境分开配置。

**downloadUrl 是 S3 公开直链**，任何客户端直接 GET 即可下载，无需鉴权参数。上传接口返回完整绝对 URL。

### 4.2 示例

```
上传:
  token     = sk-abc123...（仅用于上传鉴权）
  sessionId = 7f8d-9e0a-...
  file      = photo.png (image/png, 102KB)

S3:
  Key          = 7f8d-9e0a-.../photo.png
  Content-Type = image/png

响应:
  downloadUrl = http://192.168.1.x:9000/astron-claw-media/7f8d-9e0a-.../photo.png

下载:
  GET http://192.168.1.x:9000/astron-claw-media/7f8d-9e0a-.../photo.png
  → S3 直接返回文件（无鉴权、无过期）
```

### 4.3 不需要 mediaId

现有设计中 `mediaId`（`media_` + UUID）是一个人造标识符，用途仅为在 MySQL 中定位元数据行。迁移到 S3 后，**`{session_id}/{filename}` 本身就是自然标识符**，downloadUrl 就是 S3 直链。`mediaId` 概念不再需要。

---

## 5. 接口设计

### 5.1 上传

**POST `/api/media/upload`** — multipart/form-data

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file` | File | 是 | 上传的文件 |
| `sessionId` | String (form field) | 否 | 会话 ID。不传则服务端随机生成 UUID |
| Authorization | Header | 是 | `Bearer {token}` |

**响应**：

```json
{
  "fileName": "photo.png",
  "mimeType": "image/png",
  "fileSize": 102400,
  "sessionId": "7f8d-9e0a-...",
  "downloadUrl": "http://192.168.1.x:9000/astron-claw-media/7f8d-9e0a-.../photo.png"
}
```

变化：移除 `mediaId`，新增 `sessionId` 回显，新增 `downloadUrl`（S3 公开直链）。

### 5.2 下载

**无 Bridge 端点**。`downloadUrl` 直接指向 S3，客户端 GET 即可：

```
GET http://192.168.1.x:9000/astron-claw-media/7f8d-9e0a-.../photo.png
→ 200 OK（S3 直接返回文件）
```

前端 `<img src="downloadUrl">`、Bot `loadWebMedia(downloadUrl)` 均可直接使用。

### 5.3 错误码（上传接口）

| 状态码 | 场景 |
|--------|------|
| 401 | Token 无效或缺失 |
| 400 | 文件为空 / MIME 不允许 / 文件过大 |
| 413 | 文件超过 500MB |

---

## 6. 数据模型设计

### 6.1 S3 对象结构

| 属性 | 值 | 说明 |
|------|------|------|
| **Bucket** | `astron-claw-media` | 固定，公开读 |
| **Key** | `{session_id}/{filename}` | 上传时构造 |
| **Content-Type** | `image/png` 等 | 上传时设置 |
| **Content-Length** | 自动 | S3 自动设置 |
| **Last-Modified** | 自动 | S3 自动设置 |

不需要额外的 `x-amz-meta-*`。session_id 和 filename 已编码在 Key 路径中，Content-Type 由上传时设置。

### 6.2 S3 Bucket Policy（公开读）

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": "*",
    "Action": "s3:GetObject",
    "Resource": "arn:aws:s3:::astron-claw-media/*"
  }]
}
```

### 6.3 S3 Lifecycle Rule

```json
{
  "Rules": [{
    "ID": "expire-media-7d",
    "Status": "Enabled",
    "Expiration": { "Days": 7 },
    "Filter": { "Prefix": "" }
  }]
}
```

### 6.4 MySQL 变更

**删除 `media` 表**（Alembic migration）：

```python
def upgrade() -> None:
    op.drop_index('uk_media_media_id', table_name='media')
    op.drop_index('idx_media_expires_at', table_name='media')
    op.drop_index('idx_media_uploaded_by', table_name='media')
    op.drop_table('media')
```

### 6.5 S3 配置（新增 .env 变量）

```bash
S3_ENDPOINT=http://localhost:9000          # Bridge 内部访问 MinIO（put_object 用）
S3_PUBLIC_ENDPOINT=http://192.168.1.x:9000 # 客户端可达地址（拼 downloadUrl 用）
S3_ACCESS_KEY=minioadmin
S3_SECRET_KEY=7|0[naI0X4
S3_BUCKET=astron-claw-media
# S3_REGION 不需要配置，代码内硬编码 us-east-1（MinIO 与 aiobotocore 的共同默认值）
```

---

## 7. 变更影响分析

### 7.1 删除项

| 类型 | 目标 | 说明 |
|------|------|------|
| MySQL 表 | `media` | 整表删除，Alembic 迁移 |
| ORM Model | `Media` | 从 `models.py` 移除 |
| 方法 | `MediaManager.get_metadata()` | 不再需要 |
| 方法 | `MediaManager.get_file_path()` | 不再需要 |
| 方法 | `MediaManager.cleanup_expired()` | S3 Lifecycle 替代 |
| 路由 | `GET /api/media/download/{media_id}` | 下载直连 S3，无需 Bridge 端点 |
| 目录 | `server/media/` | 本地存储目录 |
| 概念 | `mediaId` | 由 `downloadUrl`（S3 直链）替代 |

### 7.2 新增项

| 类型 | 目标 | 说明 |
|------|------|------|
| 配置 | `S3Config` dataclass | S3 连接参数 |
| 模块 | `infra/s3.py` | aiobotocore 异步 S3 客户端封装 |
| 依赖 | `aiobotocore` | pypi 包 |

### 7.3 Server 修改项

| 文件 | 修改内容 |
|------|----------|
| `services/media_manager.py` | 重写：磁盘 + MySQL → S3 only；`store()` 返回 S3 公开直链 |
| `routers/media.py` | upload 接受 sessionId 返回 downloadUrl（S3 直链）；删除 download 路由 |
| `infra/config.py` | 新增 `S3Config` |
| `infra/models.py` | 删除 `Media` 类 |
| `app.py` | 初始化 S3 client，`MediaManager` 构造参数变更 |
| `.env` / `.env.example` | 新增 S3 配置项 |

### 7.4 Frontend 修改项

| 文件 | 修改内容 |
|------|----------|
| `frontend/index.html` | `uploadFile()` 传入 sessionId；`getMediaUrl()` 直接使用 downloadUrl（无需拼接 Bridge 地址） |

### 7.5 Plugin 修改项

| 文件 | 修改内容 |
|------|----------|
| `bridge/media.ts` | 删除 `downloadMediaFromBridge`（不再需要，downloadUrl 是公开直链）；`uploadMediaToBridge` 新增 `sessionId` 参数（作为 form field 传给 Bridge），响应适配 `{ downloadUrl }` 替代 `{ mediaId }` |
| `messaging/inbound.ts` | 入站媒体改用 `loadWebMedia(downloadUrl)` + SDK `saveMediaBuffer`（downloadUrl 是公开 URL，无需鉴权）；支持多媒体遍历；占位符改为有语义的 `<media:image>` / `<media:file name="xxx">` |
| `messaging/outbound.ts` | `agent_media` payload 用 `downloadUrl` 替代 `mediaId`；`sendMediaMessage` 新增 `sessionId` 参数，传给 `uploadMediaToBridge` 确保出站文件落入正确会话路径 |
| `messaging/handlers.ts` | 删除 `downloadAndSaveMedia` 中的 `downloadMediaFromBridge` 调用，改用 `loadWebMedia(downloadUrl)` + `saveMediaBuffer` |

### 7.6 不变项

| 组件 | 说明 |
|------|------|
| `POST /api/media/upload` | 仍为 multipart/form-data，仍需 Bearer token 鉴权 |
| Bot 出站 `loadWebMedia` → `uploadMediaToBridge` | 流程不变，仅引用格式从 mediaId → downloadUrl |

---

## 8. 关键设计决策

### 8.1 上传走 Bridge，下载直连 S3

| 场景 | 经过 Bridge | 经过 S3 | 说明 |
|------|-----------|---------|------|
| 上传 | ✅ POST /api/media/upload (需 token) | ✅ PutObject | Bridge 负责鉴权 + 校验 + 存储 |
| 下载 | ❌ | ✅ GET 公开直链 | S3 Bucket 公开读，任何客户端直连 |

上传需要 Bridge 做鉴权和校验（token、文件大小、MIME 类型），下载是公开 URL 无需中转。

### 8.2 S3 Key = `{session_id}/{filename}`

| 优点 | 说明 |
|------|------|
| 目录语义清晰 | 按 session → file 两级组织，S3 console 中浏览直观 |
| session_id 全局唯一 | UUID 天然无冲突，不需要 token 前缀做隔离 |
| 支持按 session 批量清理 | `DeleteObjects(Prefix="{session_id}/")` |
| downloadUrl 自然映射 | downloadUrl = `{S3_PUBLIC_ENDPOINT}/{BUCKET}/{key}` |
| 不需要映射表 | 无 MySQL、无 Redis、无额外索引 |
| 无敏感信息泄露 | Token 不出现在路径/URL 中 |

### 8.3 sessionId 可选，服务端兜底生成

- 前端在聊天上下文中有 `currentSessionId`，上传时传入
- Bot 插件在 `session/prompt` 上下文中有 `sessionId`，上传时传入
  - **入站**：`handleJsonRpcPrompt` 从 `params.sessionId` 取得，入站媒体 Key 自然在该 session 路径下
  - **出站**：`sendMediaMessage` 需要将 `sessionId` 透传给 `uploadMediaToBridge`，确保出站文件的 S3 Key 也落入 `{session_id}/` 路径，而非随机 UUID
- 若都未传，服务端生成随机 UUID 作为 session_id，响应中回显

### 8.4 移除 mediaId 概念

| 维度 | 旧（mediaId） | 新（downloadUrl） |
|------|-------------|-----------------|
| 标识符 | `media_7f8d9e0a...`（人造 UUID） | S3 公开直链 |
| 定位 | mediaId → MySQL → 磁盘路径 | downloadUrl 直接访问 |
| 存储 | MySQL 行 + 磁盘文件 | 仅 S3 对象 |
| 传输 | JSON-RPC payload 携带 mediaId | JSON-RPC payload 携带 downloadUrl |

### 8.5 文件覆盖

同一 `{session_id}/{filename}` 的重复上传直接覆盖，不做冲突检测。S3 PutObject 的默认行为即为覆盖，无需额外处理。

### 8.6 Bot 入站对齐钉钉/飞书模式

| 维度 | 当前（有问题） | 改进后（对齐 SDK） |
|------|-------------|-----------------|
| 下载方式 | `downloadMediaFromBridge` (带 token) | `loadWebMedia(downloadUrl)`（公开 URL，无需鉴权） |
| 存储方式 | 裸 `writeFileSync` → `/tmp/astron-claw-media/` | SDK `saveMediaBuffer`（受 `localRoots` 管理） |
| 多媒体 | 只取 `mediaItems[0]`，多文件丢数据 | 遍历所有 mediaItems，支持 `MediaPaths` |
| 占位符 | 统一写死 `"[Image]"` | 有语义：`<media:image>` / `<media:file name="report.pdf">` / `<media:audio>` |
| 清理 | 无自动清理，`/tmp` 持续膨胀 | SDK 管理生命周期，自动清理 |

---

## 9. 模块划分

| 模块 | 文件 | 变更类型 | 说明 |
|------|------|----------|------|
| S3 Client | `server/infra/s3.py` | **新增** | aiobotocore 封装（put_object, ensure_bucket） |
| Config | `server/infra/config.py` | 修改 | 新增 `S3Config` |
| MediaManager | `server/services/media_manager.py` | **重写** | 磁盘 + MySQL → S3，返回公开直链 |
| Media Router | `server/routers/media.py` | 修改 | upload 返回 S3 直链，删除 download 路由 |
| ORM Model | `server/infra/models.py` | 修改 | 删除 `Media` 类 |
| Alembic | `server/migrations/versions/` | **新增** | DROP TABLE media |
| App | `server/app.py` | 修改 | S3 初始化 |
| Frontend | `frontend/index.html` | 修改 | upload 传 sessionId，downloadUrl 直接使用 |
| Plugin media | `plugin/src/bridge/media.ts` | 修改 | 删除 downloadMediaFromBridge，upload 适配 |
| Plugin inbound | `plugin/src/messaging/inbound.ts` | 修改 | loadWebMedia + saveMediaBuffer，多媒体，语义占位符 |
| Plugin outbound | `plugin/src/messaging/outbound.ts` | 修改 | payload 用 downloadUrl |
| Plugin handlers | `plugin/src/messaging/handlers.ts` | 修改 | loadWebMedia + saveMediaBuffer |
| Server Tests | `server/tests/test_media_manager.py` | **重写** | mock S3 |
| Server E2E | `server/tests/e2e/test_integration.py` | 修改 | 适配公开直链 |

---

## 10. 实现计划

| 步骤 | 内容 | 涉及文件 |
|------|------|----------|
| 1 | 安装 `aiobotocore`，新增 `S3Config` + `.env` 配置 | `requirements.txt`, `config.py`, `.env` |
| 2 | 新增 `infra/s3.py`：封装 put_object, ensure_bucket, set_public_read_policy | `s3.py` |
| 3 | MinIO 创建 bucket + 配置公开读 Policy + Lifecycle Rule | 运维命令 |
| 4 | 重写 `MediaManager`：`store()` → S3 put + 返回公开直链 | `media_manager.py` |
| 5 | 更新 `media.py` router：upload 返回 S3 直链，删除 download 路由 | `media.py` |
| 6 | 删除 `Media` ORM Model + Alembic 迁移 DROP TABLE | `models.py`, `migrations/` |
| 7 | 更新 `app.py`：初始化 S3 client，传入 MediaManager | `app.py` |
| 8 | 更新前端：`uploadFile()` 传 sessionId，downloadUrl 直接用于渲染 | `index.html` |
| 9 | 更新 `bridge/media.ts`：删除 `downloadMediaFromBridge`，upload 响应适配 | `media.ts` |
| 10 | 更新 `inbound.ts`：`loadWebMedia(downloadUrl)` + `saveMediaBuffer`，多媒体遍历，语义占位符 | `inbound.ts`, `handlers.ts` |
| 11 | 更新 `outbound.ts`：`agent_media` payload 用 downloadUrl 替代 mediaId | `outbound.ts` |
| 12 | 重写 server 单元测试 | `test_media_manager.py` |
| 13 | 更新 E2E 测试 | `test_integration.py` |
| 14 | 清理：删除 `server/media/` 目录 | 运维 |

---

确认后可进入代码实现阶段。
