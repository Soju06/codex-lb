"""Merge the Astra notes key opt-in with main's SCIM and overflow merge head."""

revision = "20260924_000000_merge_astra_notes_and_scim_overflow_heads"
down_revision = (
    "20260918_000000_merge_scim_and_overflow_heads",
    "20260923_000000_add_api_key_astra_notes",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
