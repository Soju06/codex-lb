"""Keep source credential evidence after live ownership expiry."""

import sqlalchemy as sa
from alembic import op

revision = "20260926_010000_add_source_ownership_history"
down_revision = "20260926_000000_add_source_ownership"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("request_logs", sa.Column("model_source_revision", sa.String(64), nullable=True))
    op.create_table(
        "model_source_ownership_history",
        sa.Column("reference_key", sa.String(64), nullable=False),
        sa.Column("source_id", sa.String(), nullable=False),
        sa.Column("source_revision", sa.String(64), nullable=False),
        sa.Column("recorded_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("reference_key", "source_id", "source_revision"),
    )
    op.create_index(
        "ix_model_source_ownership_history_reference_key",
        "model_source_ownership_history",
        ["reference_key"],
    )
    # Preserve rows created by the preceding migration before this history
    # table existed.
    op.execute(
        sa.text(
            "INSERT INTO model_source_ownership_history "
            "(reference_key, source_id, source_revision) "
            "SELECT reference_key, source_id, source_revision FROM model_source_ownership"
        )
    )


def downgrade() -> None:
    op.drop_index(
        "ix_model_source_ownership_history_reference_key",
        table_name="model_source_ownership_history",
    )
    op.drop_table("model_source_ownership_history")
    op.drop_column("request_logs", "model_source_revision")
