"""merge dashboard guest access and account alias heads

Revision ID: 20260521_000000_merge_dashboard_guest_and_api_key_heads
Revises: 20260518_120000_add_dashboard_guest_access,
    20260513_000000_add_accounts_alias
Create Date: 2026-05-21
"""

from __future__ import annotations

# revision identifiers, used by Alembic.
revision = "20260521_000000_merge_dashboard_guest_and_api_key_heads"
down_revision = (
    "20260518_120000_add_dashboard_guest_access",
    "20260513_000000_add_accounts_alias",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
