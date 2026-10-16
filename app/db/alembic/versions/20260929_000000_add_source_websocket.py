"""Opt model sources into native Responses WebSocket explicitly."""

import sqlalchemy as sa
from alembic import op

revision = "20260929_000000_add_source_websocket"
down_revision = "20260926_010000_add_source_ownership_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "model_sources",
        sa.Column("supports_responses_websocket", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("model_sources", "supports_responses_websocket")
