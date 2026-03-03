"""add auto-increment id primary key to all tables

Revision ID: 6c68fd2d09ca
Revises: 260572e0c2da
Create Date: 2026-03-03 23:15:00.315682

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6c68fd2d09ca'
down_revision: Union[str, Sequence[str], None] = '260572e0c2da'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (table_name, old_pk_column)
_TABLES = [
    ('tokens', 'token'),
    ('admin_config', 'key'),
    ('media', 'media_id'),
]


def upgrade() -> None:
    """Upgrade schema: add auto-increment id PK, demote old PK to unique index."""
    for table, old_pk in _TABLES:
        # MySQL requires DROP PRIMARY KEY + ADD new column + ADD PRIMARY KEY
        # in a single ALTER TABLE to avoid intermediate invalid states.
        op.execute(sa.text(
            f"ALTER TABLE `{table}` "
            f"DROP PRIMARY KEY, "
            f"ADD COLUMN `id` INT NOT NULL AUTO_INCREMENT COMMENT '自增主键' FIRST, "
            f"ADD PRIMARY KEY (`id`)"
        ))
        # Add unique index on the old PK column
        op.create_index(f'uk_{table}_{old_pk}', table, [old_pk], unique=True)


def downgrade() -> None:
    """Downgrade schema: restore old string PK, remove id column."""
    for table, old_pk in reversed(_TABLES):
        op.drop_index(f'uk_{table}_{old_pk}', table_name=table)
        op.execute(sa.text(
            f"ALTER TABLE `{table}` "
            f"DROP PRIMARY KEY, "
            f"DROP COLUMN `id`, "
            f"ADD PRIMARY KEY (`{old_pk}`)"
        ))
