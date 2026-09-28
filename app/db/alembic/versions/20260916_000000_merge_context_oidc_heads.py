"""Join deployed context ownership with the current upstream OIDC migrations."""

import sqlalchemy as sa
from alembic import op

revision = "20260916_000000_merge_context_oidc_heads"
down_revision = (
    "20260911_020000_merge_context_auth_provider_heads",
    "20260913_000000_add_oidc_provider_flow",
)
branch_labels = None
depends_on = None


_SENTINEL = "codex_context_oidc_merge_applied"


def upgrade() -> None:
    op.get_bind().execute(
        sa.text("INSERT INTO runtime_sentinels (name, value) VALUES (:name, :value)"),
        {"name": _SENTINEL, "value": revision},
    )


def downgrade() -> None:
    op.get_bind().execute(sa.text("DELETE FROM runtime_sentinels WHERE name = :name"), {"name": _SENTINEL})
