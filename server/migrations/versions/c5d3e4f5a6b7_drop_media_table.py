"""drop media table (migrated to S3)

Revision ID: c5d3e4f5a6b7
Revises: b4c2d3e5f6a7
Create Date: 2026-03-09 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c5d3e4f5a6b7'
down_revision: Union[str, Sequence[str], None] = 'b4c2d3e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index('uk_media_media_id', table_name='media')
    op.drop_index('idx_media_expires_at', table_name='media')
    op.drop_index('idx_media_uploaded_by', table_name='media')
    op.drop_table('media')


def downgrade() -> None:
    op.create_table(
        'media',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False, comment='自增主键'),
        sa.Column('media_id', sa.String(64), nullable=False, comment='媒体文件唯一标识，media_ 前缀 + UUID hex'),
        sa.Column('file_name', sa.String(255), nullable=False, comment='原始文件名'),
        sa.Column('mime_type', sa.String(128), nullable=False, comment='MIME 类型'),
        sa.Column('file_size', sa.BigInteger(), nullable=False, comment='文件大小（字节）'),
        sa.Column('uploaded_by', sa.String(64), nullable=False, comment='上传者令牌'),
        sa.Column('uploaded_at', sa.Double(), nullable=False, comment='上传时间'),
        sa.Column('expires_at', sa.Double(), nullable=False, comment='过期时间'),
        sa.PrimaryKeyConstraint('id'),
        comment='媒体文件元数据表',
    )
    op.create_index('uk_media_media_id', 'media', ['media_id'], unique=True)
    op.create_index('idx_media_expires_at', 'media', ['expires_at'])
    op.create_index('idx_media_uploaded_by', 'media', ['uploaded_by'])
