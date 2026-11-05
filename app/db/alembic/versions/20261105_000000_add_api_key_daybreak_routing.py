"""Add per-key automatic Daybreak routing, preserving existing keys' behavior."""

import sqlalchemy as sa
from alembic import op

revision = "20261105_000000_add_api_key_daybreak_routing"
down_revision = "20260918_000000_merge_scim_and_overflow_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("api_keys")}
    if "automatic_daybreak_routing" not in columns:
        op.add_column(
            "api_keys", sa.Column("automatic_daybreak_routing", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("api_keys")}
    if "automatic_daybreak_routing" in columns:
        op.drop_column("api_keys", "automatic_daybreak_routing")
