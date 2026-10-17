"""Replace the upstream_stream_transport "default" sentinel with "auto".

Revision ID: 20260908_000000_replace_upstream_stream_transport_default_sentinel
Revises: 20260830_000000_add_quota_warmup_claim_expiry
Create Date: 2026-09-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260908_000000_replace_upstream_stream_transport_default_sentinel"
down_revision = "20260830_000000_add_quota_warmup_claim_expiry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # "default" used to mean "defer to CODEX_LB_UPSTREAM_STREAM_TRANSPORT",
    # whose own default was "auto". The env var is removed and the dashboard
    # row is the only source, so the sentinel maps to the documented default.
    # A migration cannot read the environment the removed variable was set in;
    # an operator who pinned the env var to http/websocket must re-pin it in
    # the dashboard (release notes).
    op.execute(
        sa.text(
            "UPDATE dashboard_settings SET upstream_stream_transport = 'auto' "
            "WHERE upstream_stream_transport = 'default'"
        )
    )
    with op.batch_alter_table("dashboard_settings") as batch_op:
        batch_op.alter_column(
            "upstream_stream_transport",
            existing_type=sa.String(),
            existing_nullable=False,
            server_default=sa.text("'auto'"),
        )


def downgrade() -> None:
    # "auto" is a valid value in the previous schema too, so rows are left as
    # they are; only the server default is restored.
    with op.batch_alter_table("dashboard_settings") as batch_op:
        batch_op.alter_column(
            "upstream_stream_transport",
            existing_type=sa.String(),
            existing_nullable=False,
            server_default=sa.text("'default'"),
        )
