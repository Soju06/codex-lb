"""merge parallel 012 revisions into a single head

Revision ID: 013_merge_012_firewall_and_import_heads
Revises: 012_add_api_firewall_allowlist, 012_add_import_without_overwrite_and_drop_accounts_email_unique
Create Date: 2026-02-26
"""

from __future__ import annotations

# revision identifiers, used by Alembic.
revision = "013_merge_012_firewall_and_import_heads"
down_revision = (
    "012_add_api_firewall_allowlist",
    "012_add_import_without_overwrite_and_drop_accounts_email_unique",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    return


def downgrade() -> None:
    return
