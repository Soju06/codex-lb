"""Add per-key automatic Astra notes activation, default off."""

import sqlalchemy as sa
from alembic import op

revision = "20260923_000000_add_api_key_astra_notes"
down_revision = "20260922_000000_merge_context_scim_overflow_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("api_keys")}
    if "auto_enable_astra_notes" not in columns:
        op.add_column(
            "api_keys",
            sa.Column("auto_enable_astra_notes", sa.Boolean(), nullable=False, server_default=sa.false()),
        )


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("api_keys")}
    if "auto_enable_astra_notes" in columns:
        with op.batch_alter_table("api_keys") as batch_op:
            batch_op.drop_column("auto_enable_astra_notes")
