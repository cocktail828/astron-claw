"""drop chat_active_sessions table

Revision ID: b4c2d3e5f6a7
Revises: a3b1c2d4e5f6
Create Date: 2026-03-08 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b4c2d3e5f6a7'
down_revision: Union[str, Sequence[str], None] = 'a3b1c2d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index('uk_chat_active_sessions_token', table_name='chat_active_sessions')
    op.drop_table('chat_active_sessions')


def downgrade() -> None:
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
