from datetime import datetime

from sqlalchemy import String, Text, Integer, DateTime, Index
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Token(Base):
    """API 访问令牌"""

    __tablename__ = "tokens"
    __table_args__ = (
        Index("idx_tokens_expires_at", "expires_at"),
        Index("uk_tokens_token", "token", unique=True),
        {"comment": "API 访问令牌表，管理 bot 和 chat 客户端的接入凭证"},
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True,
        comment="自增主键",
    )
    token: Mapped[str] = mapped_column(
        String(64), nullable=False,
        comment="令牌值，sk- 前缀 + 24字节hex，全局唯一标识",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False,
        comment="创建时间（UTC）",
    )
    name: Mapped[str] = mapped_column(
        String(255), nullable=False, default="",
        comment="令牌名称，便于管理员识别用途",
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False,
        comment="过期时间（UTC），9999-12-31 23:59:59 表示永不过期",
    )


class AdminConfig(Base):
    """管理后台配置"""

    __tablename__ = "admin_config"
    __table_args__ = (
        Index("uk_admin_config_key", "key", unique=True),
        {"comment": "管理后台配置表，存储管理员密码哈希等系统配置"},
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True,
        comment="自增主键",
    )
    key: Mapped[str] = mapped_column(
        String(64), nullable=False,
        comment="配置键名，如 password_salt、password_hash",
    )
    value: Mapped[str] = mapped_column(
        Text, nullable=False,
        comment="配置值，存储对应键的具体内容",
    )


class ChatSession(Base):
    """聊天会话"""

    __tablename__ = "chat_sessions"
    __table_args__ = (
        Index("idx_chat_sessions_token", "token"),
        Index("uk_chat_sessions_session_id", "session_id", unique=True),
        Index("idx_chat_sessions_created_at", "created_at"),
        {"comment": "聊天会话表，记录每个 token 下的所有会话"},
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True,
        comment="自增主键",
    )
    token: Mapped[str] = mapped_column(
        String(64), nullable=False,
        comment="关联 API 令牌",
    )
    session_id: Mapped[str] = mapped_column(
        String(36), nullable=False,
        comment="UUID 会话标识",
    )
    session_number: Mapped[int] = mapped_column(
        Integer, nullable=False,
        comment="该 token 下的序号（从 1 开始）",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False,
        comment="创建时间（UTC）",
    )
