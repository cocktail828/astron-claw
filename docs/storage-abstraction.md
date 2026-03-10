# 技术方案：对象存储抽象层（OSS Provider Abstraction）

## 1. 技术方案概述

当前系统的文件上传硬绑定 S3/MinIO（`server/infra/s3.py`），`MediaManager` 直接依赖 `S3Storage` 具体类。为支持多种 OSS 后端（S3、讯飞网关存储等），需将存储层抽象为 ABC + 工厂模式，通过环境变量 `OSS_TYPE` 在运行时选择具体实现。

### 核心目标

- 定义统一的 `ObjectStorage` 抽象接口
- 将现有 `S3Storage` 迁移为抽象接口的实现
- 新增 `IFlyGatewayStorage` 实现讯飞网关存储
- 上层 `MediaManager` / `app.py` 仅依赖抽象接口
- 通过 `OSS_TYPE` 环境变量切换存储后端，零代码改动

### 约束条件

- Python ≥ 3.11，asyncio 异步
- 不保留旧 `S3_*` 环境变量兼容，统一为 `OSS_*`
- 连接池化由各实现内部保证（aiobotocore / aiohttp 均已内置）

---

## 2. 技术选型

| 维度 | 选型 | 理由 |
|------|------|------|
| 抽象方式 | `abc.ABC` + `@abstractmethod` | Python 标准库，无额外依赖，IDE 自动提示未实现方法 |
| 工厂模式 | 函数工厂 `create_storage()` | 两种后端，无需注册表等重型机制 |
| S3 客户端 | `aiobotocore`（已有） | 保持不变 |
| 讯飞网关 HTTP | `aiohttp` | 轻量异步 HTTP 客户端，`aiobotocore` 已间接依赖 aiohttp，无额外引入成本 |
| HMAC 签名 | `hashlib` + `hmac` + `base64`（标准库） | 讯飞网关要求 HMAC-SHA256 签名，无需第三方库 |

**备注**：`aiobotocore` 底层依赖 `aiohttp`，因此项目运行时已有 `aiohttp`。但当前 `pyproject.toml` 未显式声明，需补充为直接依赖以确保讯飞网关场景下也有明确依赖。

---

## 3. 架构设计

### 3.1 分层架构

```
表现层 (Routers)
  │  routers/media.py    POST /api/media/upload
  │  routers/admin.py    POST /api/admin/cleanup
  ▼
业务层 (Services)
  │  services/media_manager.py   MediaManager(storage: ObjectStorage)
  ▼
存储抽象层 (Infra)
  │  infra/storage/base.py       ObjectStorage (ABC)
  │  infra/storage/__init__.py   create_storage() 工厂
  ▼
存储实现层
  ├─ infra/storage/s3.py            S3Storage (aiobotocore)
  └─ infra/storage/ifly_gateway.py  IFlyGatewayStorage (aiohttp + HMAC)
       └─ infra/storage/hmac_auth.py  签名工具
```

### 3.2 依赖关系图

```
┌──────────────────────────────────────────────────────────┐
│  app.py (lifespan)                                       │
│    config = load_config()                                │
│    storage = create_storage(config.storage)              │
│    await storage.start()                                 │
│    await storage.ensure_bucket()                         │
│    state.media_manager = MediaManager(storage)           │
│    ...                                                   │
│    await storage.close()                                 │
└──────────────────┬───────────────────────────────────────┘
                   │
         ┌─────────▼──────────┐
         │   ObjectStorage     │  ← ABC (infra/storage/base.py)
         │   + start()         │
         │   + close()         │
         │   + ensure_bucket() │
         │   + put_object()    │
         │   + bucket          │
         └────────┬───────────┘
                  │
       ┌──────────┼───────────────┐
       │                          │
┌──────▼───────┐         ┌───────▼─────────┐
│  S3Storage   │         │ IFlyGateway     │
│  (s3.py)     │         │ Storage         │
│  aiobotocore │         │ (ifly_gateway)  │
└──────────────┘         │ aiohttp + HMAC  │
                         └─────────────────┘
```

### 3.3 关键设计决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| ABC vs Protocol | ABC | 需要强制实现检查，Protocol 仅做静态类型提示，运行时不报错 |
| `ensure_bucket()` 留在 ABC | 是 | S3 需要建桶，讯飞直接 `pass`，保持生命周期一致 |
| `put_object` 返回值 | `str`（下载 URL） | S3 返回拼接的公网 URL；讯飞网关由服务端返回临时链接。上层无需关心差异 |
| `put_object` body 类型 | `Union[bytes, BinaryIO]` | S3 支持流式上传（`SpooledTemporaryFile`），讯飞网关需要 `bytes`，由各实现自行处理转换 |
| 连接池化 | 各实现内置 | S3: aiobotocore 持久 client；讯飞: aiohttp.ClientSession (TCPConnector limit=100) |

---

## 4. 模块划分

### 4.1 新增文件

```
server/infra/storage/
├── __init__.py          # 导出 ObjectStorage, create_storage
├── base.py              # ABC 定义
├── s3.py                # S3Storage（从 infra/s3.py 迁移）
├── ifly_gateway.py      # IFlyGatewayStorage
└── hmac_auth.py         # HMAC-SHA256 签名工具
```

### 4.2 删除的文件

| 文件 | 说明 |
|------|------|
| `server/infra/s3.py` | 迁移至 `infra/storage/s3.py`，原文件删除 |

### 4.3 修改的文件

| 文件 | 当前代码 | 改动说明 |
|------|----------|----------|
| `infra/config.py` | `S3Config` + `AppConfig.s3` | `S3Config` → `StorageConfig`（增加 `type`、`ttl` 字段）；`AppConfig.s3` → `AppConfig.storage`；`load_config()` 中 `S3_*` 环境变量全部替换为 `OSS_*` |
| `app.py` | `from infra.s3 import S3Storage` + `S3Storage(config.s3)` | 改为 `from infra.storage import create_storage` + `create_storage(config.storage)` |
| `services/media_manager.py` | `from infra.s3 import S3Storage` + `__init__(self, s3: S3Storage)` | 改为 `from infra.storage import ObjectStorage` + `__init__(self, storage: ObjectStorage)` |
| `routers/admin.py` | 第 118 行 `media_manager.cleanup_expired()` | 移除此调用（方法在 S3 迁移时已删除，当前会 `AttributeError`），移除返回值中的 `removed_media` 字段 |
| `pyproject.toml` | 无 `aiohttp` | 新增 `aiohttp` 显式依赖 |
| `.env.example` | `S3_*` 变量 | 替换为 `OSS_*` 变量 |
| `tests/test_media_manager.py` | mock fixture 名 `mock_s3` | 重命名为 `mock_storage`，无逻辑变化 |

---

## 5. 接口设计

### 5.1 ABC — `ObjectStorage`

```python
# server/infra/storage/base.py

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import BinaryIO, Union


class ObjectStorage(ABC):
    """Abstract base class for object storage backends."""

    @abstractmethod
    async def start(self) -> None:
        """Initialize connections. Call once at application startup."""

    @abstractmethod
    async def close(self) -> None:
        """Release resources. Call once at application shutdown."""

    @abstractmethod
    async def ensure_bucket(self) -> None:
        """Ensure the storage bucket/namespace exists and is ready."""

    @abstractmethod
    async def put_object(
        self,
        key: str,
        body: Union[bytes, BinaryIO],
        content_type: str,
        content_length: int | None = None,
    ) -> str:
        """Upload an object and return its public download URL.

        Args:
            key: Object key (e.g. "{session_id}/{filename}").
            body: File content — raw bytes or seekable file-like object.
            content_type: MIME type of the object.
            content_length: Optional byte length hint.

        Returns:
            Public download URL as a string.
        """

    @property
    @abstractmethod
    def bucket(self) -> str:
        """Bucket or namespace name (used for logging)."""
```

### 5.2 工厂函数 — `create_storage()`

```python
# server/infra/storage/__init__.py

from infra.storage.base import ObjectStorage


def create_storage(config: "StorageConfig") -> ObjectStorage:
    """Create a storage backend based on config.type.

    Lazy-imports concrete implementations to avoid pulling in
    unnecessary dependencies (e.g. aiobotocore when using ifly_gateway).
    """
    if config.type == "s3":
        from infra.storage.s3 import S3Storage
        return S3Storage(config)
    elif config.type == "ifly_gateway":
        from infra.storage.ifly_gateway import IFlyGatewayStorage
        return IFlyGatewayStorage(config)
    else:
        raise ValueError(f"Unknown storage type: '{config.type}'")
```

### 5.3 HMAC 签名 — `build_auth_header()`

```python
# server/infra/storage/hmac_auth.py

def build_auth_header(
    url: str,
    method: str,
    api_key: str,
    api_secret: str,
) -> dict[str, str]:
    """Build HMAC-SHA256 authentication headers for iFlytek Gateway.

    Signature string format:
        host: {host}
        date: {rfc2822_date}
        {METHOD} {path} HTTP/1.1
        digest: SHA-256={base64(sha256(""))}

    Returns:
        Dict with Host, Date, Digest, Authorization headers.
    """
```

---

## 6. 数据模型

### 6.1 `StorageConfig`（替代原 `S3Config`）

```python
# server/infra/config.py

@dataclass(frozen=True)
class StorageConfig:
    type: str               # "s3" | "ifly_gateway"
    endpoint: str           # 服务端上传端点
    public_endpoint: str    # 客户端下载端点（S3 用，讯飞由服务端返回）
    access_key: str         # 认证 key
    secret_key: str         # 认证 secret
    bucket: str             # 桶名 / 命名空间
    region: str = "us-east-1"    # S3 专用，讯飞忽略
    ttl: int = 157788000         # 讯飞网关链接有效期(秒)，S3 忽略
```

### 6.2 环境变量映射

```env
# ── Object Storage ──────────────────────────────
OSS_TYPE=s3                                 # s3 | ifly_gateway
OSS_ENDPOINT=http://localhost:9000          # 上传端点
OSS_ACCESS_KEY=minioadmin                   # 认证 key
OSS_SECRET_KEY=minioadmin                   # 认证 secret
OSS_BUCKET=astron-claw-media                # 桶名

# S3 专用
OSS_PUBLIC_ENDPOINT=http://localhost:9000   # 下载端点（不设则 = OSS_ENDPOINT）
OSS_REGION=us-east-1

# 讯飞网关专用
OSS_TTL=157788000                           # 下载链接有效期(秒)，约 5 年
```

### 6.3 `load_config()` 加载逻辑

```python
storage=StorageConfig(
    type=os.getenv("OSS_TYPE", "s3"),
    endpoint=os.getenv("OSS_ENDPOINT", "http://localhost:9000"),
    public_endpoint=(
        os.getenv("OSS_PUBLIC_ENDPOINT")
        or os.getenv("OSS_ENDPOINT", "http://localhost:9000")
    ),
    access_key=os.getenv("OSS_ACCESS_KEY", "minioadmin"),
    secret_key=os.getenv("OSS_SECRET_KEY", "minioadmin"),
    bucket=os.getenv("OSS_BUCKET", "astron-claw-media"),
    region=os.getenv("OSS_REGION", "us-east-1"),
    ttl=int(os.getenv("OSS_TTL", "157788000")),
),
```

---

## 7. 各实现详细设计

### 7.1 S3Storage 改造

**改动极小**，仅两处：

| # | 当前代码 | 改为 |
|---|----------|------|
| 1 | `class S3Storage:` | `class S3Storage(ObjectStorage):` |
| 2 | `from infra.config import S3Config` | `from infra.config import StorageConfig` |

其余全部保留：
- 持久 client 连接管理（`start()` / `close()`）
- `ensure_bucket()` 跳过已存在桶 + 仅新桶设置 policy/lifecycle
- `put_object()` 文本 charset 补全 + URL 编码
- `_LIFECYCLE_RULE_7D` 常量

### 7.2 IFlyGatewayStorage 实现

```python
# server/infra/storage/ifly_gateway.py

class IFlyGatewayStorage(ObjectStorage):

    def __init__(self, config: StorageConfig):
        self._config = config
        self._session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        """创建 aiohttp session（内置 TCPConnector 连接池）。"""
        self._session = aiohttp.ClientSession()

    async def close(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    async def ensure_bucket(self) -> None:
        """讯飞网关无需预创建桶，空实现。"""
        pass

    async def put_object(
        self,
        key: str,
        body: Union[bytes, BinaryIO],
        content_type: str,
        content_length: int | None = None,
    ) -> str:
        """上传文件到讯飞网关，返回带签名的临时下载链接。

        流程：
        1. 从 key 提取 filename（取最后一段路径）
        2. body 若为 BinaryIO 则 read() 转 bytes
        3. 构造请求 URL:
           {endpoint}/api/v1/{bucket}?get_link=true&link_ttl={ttl}&filename={name}&expose=true
        4. build_auth_header() 生成 HMAC-SHA256 认证头
        5. POST file_bytes → 解析 JSON
        6. 返回 ret["data"]["link"]
        """

    @property
    def bucket(self) -> str:
        return self._config.bucket
```

**连接池说明**：`aiohttp.ClientSession` 默认使用 `TCPConnector(limit=100)`，单个 session 贯穿应用生命周期，TCP 连接自动复用，无需额外配置。

### 7.3 HMAC 签名工具

```python
# server/infra/storage/hmac_auth.py
# 使用标准库：hashlib, hmac, base64, urllib.parse, wsgiref.handlers

def build_auth_header(url, method, api_key, api_secret) -> dict[str, str]:
    """
    1. 解析 URL 取 host、path
    2. 生成 RFC 2822 格式时间戳
    3. 计算空 body 的 SHA-256 digest
    4. 拼接签名串：host + date + request-line + digest
    5. HMAC-SHA256 签名
    6. 组装 Authorization header:
       api_key="...", algorithm="hmac-sha256",
       headers="host date request-line digest",
       signature="..."
    7. 返回 {Host, Date, Digest, Authorization, Method}
    """
```

---

## 8. 顺带修复：`admin.py` 的 `cleanup_expired` 调用

**现状**：`routers/admin.py:118` 调用了 `state.media_manager.cleanup_expired()`，但此方法在 S3 迁移时已删除（S3 Lifecycle 自动过期），当前调用会触发 `AttributeError`。

**修复**：

```python
# 修改前 (admin.py:112-121)
@router.post("/api/admin/cleanup")
async def admin_cleanup(admin_session: str | None = Cookie(default=None)):
    denied = await _require_admin(admin_session)
    if denied:
        return denied
    token_count = await state.token_manager.cleanup_expired()
    media_count = await state.media_manager.cleanup_expired()  # ← BUG
    session_count = await state.bridge.cleanup_old_sessions(max_age_days=30)
    return {"removed_tokens": token_count, "removed_media": media_count, ...}

# 修改后
@router.post("/api/admin/cleanup")
async def admin_cleanup(admin_session: str | None = Cookie(default=None)):
    denied = await _require_admin(admin_session)
    if denied:
        return denied
    token_count = await state.token_manager.cleanup_expired()
    session_count = await state.bridge.cleanup_old_sessions(max_age_days=30)
    return {"removed_tokens": token_count, "removed_sessions": session_count}
```

---

## 9. 实现计划

| 步骤 | 内容 | 涉及文件 | 依赖 |
|------|------|----------|------|
| 1 | 创建 `infra/storage/` 包，定义 ABC + 工厂函数 | `base.py`, `__init__.py` | — |
| 2 | 迁移 S3Storage 到新位置，继承 ABC | `storage/s3.py` | 步骤 1 |
| 3 | 实现 HMAC 签名工具 | `storage/hmac_auth.py` | — |
| 4 | 实现 IFlyGatewayStorage | `storage/ifly_gateway.py` | 步骤 1, 3 |
| 5 | 重构 `StorageConfig` 替代 `S3Config`，统一 `OSS_*` 环境变量 | `config.py` | — |
| 6 | 更新 `app.py` 使用 `create_storage()` 工厂 | `app.py` | 步骤 1, 5 |
| 7 | 更新 `MediaManager` 依赖 `ObjectStorage` 抽象类型 | `media_manager.py` | 步骤 1 |
| 8 | 修复 `admin.py` 移除 `cleanup_expired()` 调用 | `admin.py` | — |
| 9 | 删除旧 `infra/s3.py` | — | 步骤 2 |
| 10 | 新增 `aiohttp` 显式依赖 | `pyproject.toml` | — |
| 11 | 更新 `.env.example`（`S3_*` → `OSS_*`） | `.env.example` | 步骤 5 |
| 12 | 更新单元测试 mock 类型 | `tests/test_media_manager.py` | 步骤 7 |

---

确认后可使用「代码实现」进入编码阶段。
