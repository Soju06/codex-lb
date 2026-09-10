"""Add opt-in CPA discovery and persisted refresh fencing."""

import sqlalchemy as sa
from alembic import op

revision = "20260910_000000_add_cpa_catalog_discovery"
down_revision = "20260910_000000_request_logs_missing_cost_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing_columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("model_sources")}
    columns = (
        sa.Column("catalog_mode", sa.String(), server_default="manual", nullable=False),
        sa.Column("catalog_refresh_token", sa.String(), nullable=True),
        sa.Column("catalog_next_refresh_at", sa.DateTime(), nullable=True),
    )
    with op.batch_alter_table("model_sources") as batch:
        for column in columns:
            if column.name not in existing_columns:
                batch.add_column(column)


def downgrade() -> None:
    with op.batch_alter_table("model_sources") as batch:
        batch.drop_column("catalog_next_refresh_at")
        batch.drop_column("catalog_refresh_token")
        batch.drop_column("catalog_mode")
