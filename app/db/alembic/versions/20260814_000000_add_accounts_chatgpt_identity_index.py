"""add account ChatGPT identity lookup index

Revision ID: 20260814_000000_add_accounts_chatgpt_identity_index
Revises: 20260816_000000_add_model_source_embeddings
Create Date: 2026-08-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260814_000000_add_accounts_chatgpt_identity_index"
down_revision = "20260816_000000_add_model_source_embeddings"
branch_labels = None
depends_on = None

_INDEX = "idx_accounts_chatgpt_account_id"
_TABLE = "accounts"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute(sa.text(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {_INDEX} ON {_TABLE} (chatgpt_account_id)"))
    else:
        op.execute(sa.text(f"CREATE INDEX IF NOT EXISTS {_INDEX} ON {_TABLE} (chatgpt_account_id)"))


def downgrade() -> None:
    op.drop_index(_INDEX, table_name=_TABLE, if_exists=True)
