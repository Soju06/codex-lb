"""merge request log useragent and upstream proxy routing heads

Revision ID: 20260602_080000_merge_useragent_and_upstream_proxy_heads
Revises: 20260602_050000_add_upstream_proxy_routing, 20260602_070000_add_request_log_useragent_fields
Create Date: 2026-06-02 08:00:00.000000
"""

from __future__ import annotations

revision = "20260602_080000_merge_useragent_and_upstream_proxy_heads"
down_revision = (
    "20260602_050000_add_upstream_proxy_routing",
    "20260602_070000_add_request_log_useragent_fields",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
