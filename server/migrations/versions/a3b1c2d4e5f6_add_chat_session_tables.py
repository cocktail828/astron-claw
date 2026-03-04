"""add chat session tables

Revision ID: a3b1c2d4e5f6
Revises: 6c68fd2d09ca
Create Date: 2026-03-04 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3b1c2d4e5f6'
down_revision: Union[str, Sequence[str], None] = '6c68fd2d09ca'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'chat_sessions',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False, comment='自增主键'),
        sa.Column('token', sa.String(64), nullable=False, comment='关联 API 令牌'),
        sa.Column('session_id', sa.String(36), nullable=False, comment='UUID 会话标识'),
        sa.Column('session_number', sa.Integer(), nullable=False, comment='该 token 下的序号（从 1 开始）'),
        sa.Column('created_at', sa.Double(), nullable=False, comment='创建时间，Unix 时间戳（秒）'),
        sa.PrimaryKeyConstraint('id'),
        comment='聊天会话表，记录每个 token 下的所有会话',
    )
    op.create_index('idx_chat_sessions_token', 'chat_sessions', ['token'])
    op.create_index('uk_chat_sessions_session_id', 'chat_sessions', ['session_id'], unique=True)
    op.create_index('idx_chat_sessions_created_at', 'chat_sessions', ['created_at'])

    op.create_table(
        'chat_active_sessions',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False, comment='自增主键'),
        sa.Column('token', sa.String(64), nullable=False, comment='关联 API 令牌'),
        sa.Column('active_session_id', sa.String(36), nullable=False, comment='当前活跃会话 UUID'),
        sa.Column('updated_at', sa.Double(), nullable=False, comment='最后更新时间，Unix 时间戳（秒）'),
        sa.PrimaryKeyConstraint('id'),
        comment='活跃会话表，记录每个 token 当前活跃的会话',
    )
    op.create_index('uk_chat_active_sessions_token', 'chat_active_sessions', ['token'], unique=True)


def downgrade() -> None:
    op.drop_table('chat_active_sessions')
    op.drop_table('chat_sessions')
