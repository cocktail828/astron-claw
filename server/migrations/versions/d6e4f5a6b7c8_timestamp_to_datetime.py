"""convert timestamp Double columns to DateTime

Revision ID: d6e4f5a6b7c8
Revises: c5d3e4f5a6b7
Create Date: 2026-03-11 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd6e4f5a6b7c8'
down_revision: Union[str, None] = 'c5d3e4f5a6b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- tokens.created_at: Double → DateTime ---
    op.execute(
        "ALTER TABLE tokens ADD COLUMN created_at_dt DATETIME NULL"
    )
    op.execute("UPDATE tokens SET created_at_dt = FROM_UNIXTIME(created_at)")
    op.execute(
        "ALTER TABLE tokens DROP COLUMN created_at, "
        "CHANGE COLUMN created_at_dt created_at DATETIME NOT NULL"
    )

    # --- tokens.expires_at: Double → DateTime ---
    op.execute(
        "ALTER TABLE tokens ADD COLUMN expires_at_dt DATETIME NULL"
    )
    op.execute(
        "UPDATE tokens SET expires_at_dt = CASE "
        "WHEN expires_at >= 9999999999 THEN '9999-12-31 23:59:59' "
        "ELSE FROM_UNIXTIME(expires_at) END"
    )
    op.drop_index('idx_tokens_expires_at', table_name='tokens')
    op.execute(
        "ALTER TABLE tokens DROP COLUMN expires_at, "
        "CHANGE COLUMN expires_at_dt expires_at DATETIME NOT NULL"
    )

    # --- chat_sessions.created_at: Double → DateTime ---
    op.execute(
        "ALTER TABLE chat_sessions ADD COLUMN created_at_dt DATETIME NULL"
    )
    op.execute("UPDATE chat_sessions SET created_at_dt = FROM_UNIXTIME(created_at)")
    op.drop_index('idx_chat_sessions_created_at', table_name='chat_sessions')
    op.execute(
        "ALTER TABLE chat_sessions DROP COLUMN created_at, "
        "CHANGE COLUMN created_at_dt created_at DATETIME NOT NULL"
    )

    # --- Rebuild indexes ---
    op.create_index('idx_tokens_expires_at', 'tokens', ['expires_at'])
    op.create_index('idx_chat_sessions_created_at', 'chat_sessions', ['created_at'])


def downgrade() -> None:
    # --- tokens.created_at: DateTime → Double ---
    op.execute(
        "ALTER TABLE tokens ADD COLUMN created_at_ts DOUBLE NULL"
    )
    op.execute("UPDATE tokens SET created_at_ts = UNIX_TIMESTAMP(created_at)")
    op.execute(
        "ALTER TABLE tokens DROP COLUMN created_at, "
        "CHANGE COLUMN created_at_ts created_at DOUBLE NOT NULL"
    )

    # --- tokens.expires_at: DateTime → Double ---
    op.execute(
        "ALTER TABLE tokens ADD COLUMN expires_at_ts DOUBLE NULL"
    )
    op.execute(
        "UPDATE tokens SET expires_at_ts = CASE "
        "WHEN expires_at = '9999-12-31 23:59:59' THEN 9999999999.0 "
        "ELSE UNIX_TIMESTAMP(expires_at) END"
    )
    op.drop_index('idx_tokens_expires_at', table_name='tokens')
    op.execute(
        "ALTER TABLE tokens DROP COLUMN expires_at, "
        "CHANGE COLUMN expires_at_ts expires_at DOUBLE NOT NULL"
    )

    # --- chat_sessions.created_at: DateTime → Double ---
    op.execute(
        "ALTER TABLE chat_sessions ADD COLUMN created_at_ts DOUBLE NULL"
    )
    op.execute("UPDATE chat_sessions SET created_at_ts = UNIX_TIMESTAMP(created_at)")
    op.drop_index('idx_chat_sessions_created_at', table_name='chat_sessions')
    op.execute(
        "ALTER TABLE chat_sessions DROP COLUMN created_at, "
        "CHANGE COLUMN created_at_ts created_at DOUBLE NOT NULL"
    )

    # --- Rebuild indexes ---
    op.create_index('idx_tokens_expires_at', 'tokens', ['expires_at'])
    op.create_index('idx_chat_sessions_created_at', 'chat_sessions', ['created_at'])
