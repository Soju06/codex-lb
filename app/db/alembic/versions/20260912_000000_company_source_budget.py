"""Add optional rolling company source token budget."""

import sqlalchemy as sa
from alembic import op

revision = "20260912_000000_company_source_budget"
down_revision = "20260911_030000_add_local_login_policy"
branch_labels = None
depends_on = None

_TABLE = "model_sources"
_COLUMN = "local_token_budget"


def _has_column() -> bool:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(_TABLE):
        return False
    return any(column["name"] == _COLUMN for column in sa.inspect(bind).get_columns(_TABLE))


def upgrade() -> None:
    if not _has_column():
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.BigInteger(), nullable=True))


def downgrade() -> None:
    if _has_column():
        with op.batch_alter_table(_TABLE) as batch:
            batch.drop_column(_COLUMN)
