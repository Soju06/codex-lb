"""repair schemas stamped at the retired identity/warmup merge head

Revision ID: 20260820_000000_repair_retired_identity_and_warmup_stamp
Revises: 20260816_000000_add_model_source_embeddings
Create Date: 2026-08-20

Some local August 14, 2026 builds emitted the no-op merge stamp
``20260814_020000_merge_identity_and_warmup_heads`` even when the current
mainline file-pin, sticky-abandonment-scope, pending-deletion, API-key
reasoning-policy, and model-source-embeddings lineage had never run, while the
retired account-identity index and quota-planner lease-expiry column were
still present. Startup remaps that dead stamp to the current pre-repair head so
Alembic can continue; this forward-only repair step then replays the guarded
current migrations and drops the two stale artifacts.
"""

from __future__ import annotations

import importlib
from types import ModuleType

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260820_000000_repair_retired_identity_and_warmup_stamp"
down_revision = "20260816_000000_add_model_source_embeddings"
branch_labels = None
depends_on = None

_OBSOLETE_ACCOUNT_INDEX = "idx_accounts_chatgpt_account_id"
_OBSOLETE_QUOTA_COLUMN = "lease_expires_at"


def _migration(module_name: str) -> ModuleType:
    return importlib.import_module(f"app.db.alembic.versions.{module_name}")


def _has_table(connection: Connection, table_name: str) -> bool:
    return sa.inspect(connection).has_table(table_name)


def _columns(connection: Connection, table_name: str) -> set[str]:
    if not _has_table(connection, table_name):
        return set()
    return {column["name"] for column in sa.inspect(connection).get_columns(table_name)}


def _indexes(connection: Connection, table_name: str) -> set[str]:
    if not _has_table(connection, table_name):
        return set()
    names = (index.get("name") for index in sa.inspect(connection).get_indexes(table_name))
    return {name for name in names if name is not None}


def upgrade() -> None:
    _migration("20260813_000000_add_file_account_pins").upgrade()
    _migration("20260812_120000_add_sticky_abandonment_scope").upgrade()
    _migration("20260816_000000_add_account_pending_deletion").upgrade()
    _migration("20260806_030000_add_api_key_allowed_reasoning_efforts").upgrade()
    _migration("20260816_000000_add_model_source_embeddings").upgrade()

    connection = op.get_bind()

    if _OBSOLETE_ACCOUNT_INDEX in _indexes(connection, "accounts"):
        op.drop_index(_OBSOLETE_ACCOUNT_INDEX, table_name="accounts", if_exists=True)

    if _OBSOLETE_QUOTA_COLUMN in _columns(connection, "quota_planner_decisions"):
        with op.batch_alter_table("quota_planner_decisions") as batch_op:
            batch_op.drop_column(_OBSOLETE_QUOTA_COLUMN)


def downgrade() -> None:
    # This revision repairs databases carrying a retired local merge stamp. It
    # must not resurrect the stale index/column or remove objects owned by the
    # canonical current-main migrations it replays above.
    pass
