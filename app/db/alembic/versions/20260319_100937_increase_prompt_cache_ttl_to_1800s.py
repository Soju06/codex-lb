"""increase prompt cache ttl from 300s to 1800s

Revision ID: 20260319_100937_increase_prompt_cache_ttl_to_1800s
Revises: 20260313_024500_merge_request_log_transport_heads
Create Date: 2026-03-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision = "20260319_100937_increase_prompt_cache_ttl_to_1800s"
down_revision = "20260313_024500_merge_request_log_transport_heads"
branch_labels = None
depends_on = None


def _table_exists(connection: Connection, table_name: str) -> bool:
    inspector = sa.inspect(connection)
    return inspector.has_table(table_name)


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "dashboard_settings"):
        return

    # Update existing rows with value=300 to 1800
    bind.execute(
        sa.text(
            """
            UPDATE dashboard_settings
            SET openai_cache_affinity_max_age_seconds = 1800
            WHERE openai_cache_affinity_max_age_seconds = 300
            """
        )
    )

    # Alter the column default
    with op.batch_alter_table("dashboard_settings") as batch_op:
        batch_op.alter_column(
            "openai_cache_affinity_max_age_seconds",
            existing_type=sa.Integer(),
            server_default=sa.text("1800"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "dashboard_settings"):
        return

    # Revert existing rows with value=1800 back to 300
    bind.execute(
        sa.text(
            """
            UPDATE dashboard_settings
            SET openai_cache_affinity_max_age_seconds = 300
            WHERE openai_cache_affinity_max_age_seconds = 1800
            """
        )
    )

    # Alter the column default back to 300
    with op.batch_alter_table("dashboard_settings") as batch_op:
        batch_op.alter_column(
            "openai_cache_affinity_max_age_seconds",
            existing_type=sa.Integer(),
            server_default=sa.text("300"),
        )
