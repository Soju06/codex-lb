"""Persist explicit Desktop reset pooling and immutable redemption selection."""

import sqlalchemy as sa
from alembic import op

revision = "20260909_210000_desktop_reset_pool"
down_revision = "20260909_080000_dashboard_stream_bridge_budgets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("dashboard_settings")}
    if "desktop_reset_pool_enabled" not in columns:
        op.add_column(
            "dashboard_settings",
            sa.Column("desktop_reset_pool_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
    if not inspector.has_table("desktop_reset_credit_redemptions"):
        op.create_table(
            "desktop_reset_credit_redemptions",
            sa.Column("caller_account_id", sa.String(), primary_key=True),
            sa.Column("redeem_request_id", sa.String(), primary_key=True),
            sa.Column("owner_account_id", sa.String(), nullable=False),
            sa.Column("owner_chatgpt_account_id", sa.String(), nullable=False),
            sa.Column("credit_id", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )


def downgrade() -> None:
    if op.get_bind().execute(sa.text("SELECT 1 FROM desktop_reset_credit_redemptions LIMIT 1")).first():
        raise RuntimeError(
            "Disable reset pooling and retain its redemption ledger; existing bindings cannot be discarded"
        )
    op.drop_table("desktop_reset_credit_redemptions")
    op.drop_column("dashboard_settings", "desktop_reset_pool_enabled")
