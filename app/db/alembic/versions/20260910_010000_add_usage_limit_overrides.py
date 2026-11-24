"""Add optional window overrides to account usage limits."""

import sqlalchemy as sa
from alembic import op

revision = "20260910_010000_add_usage_limit_overrides"
down_revision = "20260728_010000_add_account_usage_limits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("accounts"):
        return
    columns = {column["name"] for column in inspector.get_columns("accounts")}
    constraints = {constraint["name"] for constraint in inspector.get_check_constraints("accounts")}
    with op.batch_alter_table("accounts") as batch:
        if "ck_accounts_usage_limit_enabled_requires_percent" in constraints:
            batch.drop_constraint("ck_accounts_usage_limit_enabled_requires_percent", type_="check")
        for window in ("5h", "weekly"):
            column = f"usage_limit_{window}_percent"
            if column not in columns:
                batch.add_column(sa.Column(column, sa.Float(), nullable=True))
            if f"ck_accounts_{column}_range" not in constraints:
                batch.create_check_constraint(
                    f"ck_accounts_{column}_range", f"{column} IS NULL OR ({column} > 0 AND {column} <= 100)"
                )
        batch.create_check_constraint(
            "ck_accounts_usage_limit_enabled_requires_percent",
            "NOT usage_limit_enabled OR usage_limit_percent IS NOT NULL "
            "OR usage_limit_5h_percent IS NOT NULL OR usage_limit_weekly_percent IS NOT NULL",
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("accounts"):
        return
    columns = {column["name"] for column in inspector.get_columns("accounts")}
    constraints = {constraint["name"] for constraint in inspector.get_check_constraints("accounts")}
    has_base_policy = {"usage_limit_enabled", "usage_limit_percent"} <= columns
    if has_base_policy:
        op.execute("UPDATE accounts SET usage_limit_enabled = false WHERE usage_limit_percent IS NULL")
    with op.batch_alter_table("accounts") as batch:
        if "ck_accounts_usage_limit_enabled_requires_percent" in constraints:
            batch.drop_constraint("ck_accounts_usage_limit_enabled_requires_percent", type_="check")
        for window in ("5h", "weekly"):
            column = f"usage_limit_{window}_percent"
            if f"ck_accounts_{column}_range" in constraints:
                batch.drop_constraint(f"ck_accounts_{column}_range", type_="check")
            if column in columns:
                batch.drop_column(column)
        if has_base_policy:
            batch.create_check_constraint(
                "ck_accounts_usage_limit_enabled_requires_percent",
                "NOT usage_limit_enabled OR usage_limit_percent IS NOT NULL",
            )
