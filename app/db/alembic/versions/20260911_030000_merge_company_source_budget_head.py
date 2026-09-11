"""Merge the company source budget migration with the current upstream head."""

revision = "20260911_030000_merge_company_source_budget_head"
down_revision = (
    "20260910_190000_company_source_budget",
    "20260911_020000_add_http_bridge_terminal_append_phase",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
