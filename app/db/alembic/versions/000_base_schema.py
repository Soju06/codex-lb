"""create base schema

Revision ID: 000_base_schema
Revises:
Create Date: 2026-02-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.db.models import Base

# revision identifiers, used by Alembic.
revision = "000_base_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    for table_name in (
        "api_keys",
        "dashboard_settings",
        "sticky_sessions",
        "request_logs",
        "usage_history",
        "accounts",
    ):
        if table_name in tables:
            op.drop_table(table_name)

    if bind.dialect.name == "postgresql":
        op.execute(sa.text("DROP TYPE IF EXISTS account_status"))
