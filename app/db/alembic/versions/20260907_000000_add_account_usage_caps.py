"""Add optional per-account 5h and weekly usage caps."""

import sqlalchemy as sa
from alembic import context, op

revision = "20260907_000000_add_account_usage_caps"
down_revision = (
    "20260908_000000_add_subscription_overflow",
    "20260908_000000_replace_upstream_stream_transport_default_sentinel",
)
branch_labels = None
depends_on = None


def _account_columns() -> set[str] | None:
    if context.is_offline_mode():
        return None
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns("accounts")}


def upgrade() -> None:
    columns = _account_columns()
    if columns is None or "usage_cap_5h_percent" not in columns:
        op.add_column("accounts", sa.Column("usage_cap_5h_percent", sa.Float(), nullable=True))
    if columns is None or "usage_cap_weekly_percent" not in columns:
        op.add_column("accounts", sa.Column("usage_cap_weekly_percent", sa.Float(), nullable=True))


def downgrade() -> None:
    columns = _account_columns()
    if columns is None or "usage_cap_weekly_percent" in columns:
        op.drop_column("accounts", "usage_cap_weekly_percent")
    if columns is None or "usage_cap_5h_percent" in columns:
        op.drop_column("accounts", "usage_cap_5h_percent")
