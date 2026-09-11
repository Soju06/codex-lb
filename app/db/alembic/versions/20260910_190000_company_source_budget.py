"""Add optional rolling company source token budget."""

import sqlalchemy as sa
from alembic import op

revision = "20260910_190000_company_source_budget"
down_revision = "20260909_040000_dashboard_timeout_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("model_sources", sa.Column("local_token_budget", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("model_sources") as batch:
        batch.drop_column("local_token_budget")
