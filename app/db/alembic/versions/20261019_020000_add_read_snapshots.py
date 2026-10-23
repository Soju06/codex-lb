"""Persist background analytical read snapshots without changing accounting tables."""

from __future__ import annotations

import re

import sqlalchemy as sa
from alembic import op

revision = "20261019_020000_add_read_snapshots"
down_revision = "20260918_000000_merge_scim_and_overflow_heads"
branch_labels = None
depends_on = None


def _columns() -> list[sa.Column]:
    return [
        sa.Column("snapshot_key", sa.String(64), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("parameters_json", sa.Text(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=True),
        sa.Column("generation", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("published_generation", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("generated_at", sa.DateTime(), nullable=True),
        sa.Column("refresh_started_at", sa.DateTime(), nullable=True),
        sa.Column("refresh_at", sa.DateTime(), nullable=False),
        sa.Column("requested_at", sa.DateTime(), nullable=False),
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_error", sa.String(128), nullable=True),
        sa.Column("duration_ms", sa.Float(), nullable=True),
    ]


def _normalized_default(value: str | None) -> str | None:
    if value is None or value.strip().upper() == "NULL":
        return None
    value = value.strip().strip("()").lower()
    # PostgreSQL reflects BIGINT literal defaults as e.g. '0'::bigint.
    # Accept only the equivalent constants, never arbitrary cast expressions.
    cast_literal = re.fullmatch(r"('(?:0|1|false|true)'|0|1|false|true)\s*::\s*(?:bigint|integer|boolean)", value)
    if cast_literal:
        value = cast_literal.group(1)
    value = value.strip("'\"")
    return {"false": "0", "true": "1"}.get(value, value)


def _validate_existing(inspector: sa.Inspector, columns: list[sa.Column]) -> bool:
    """Refuse an alien layout before accepting it OR deleting its rows."""
    existing = {column["name"]: column for column in inspector.get_columns("read_snapshots")}
    expected = {column.name: column for column in columns}
    dialect = op.get_bind().dialect
    if existing.keys() != expected.keys():
        raise RuntimeError("Unexpected existing read_snapshots layout; refusing migration")
    for name, column in expected.items():
        default = None
        if column.server_default is not None:
            if not isinstance(column.server_default, sa.schema.DefaultClause):
                raise RuntimeError("Unexpected migration column default")
            argument = column.server_default.arg
            default = (
                str(argument.compile(dialect=dialect)) if isinstance(argument, sa.sql.ClauseElement) else str(argument)
            )
        if (
            existing[name]["nullable"] != column.nullable
            or existing[name].get("computed") is not None
            or existing[name].get("identity") is not None
            or op.get_context().impl.compare_type(sa.Column(name, existing[name]["type"]), column)
            or _normalized_default(existing[name]["default"]) != _normalized_default(default)
        ):
            raise RuntimeError("Unexpected existing read_snapshots layout; refusing migration")
    if inspector.get_pk_constraint("read_snapshots")["constrained_columns"] != ["snapshot_key"]:
        raise RuntimeError("Unexpected existing read_snapshots primary key")
    if (
        inspector.get_foreign_keys("read_snapshots")
        or inspector.get_unique_constraints("read_snapshots")
        or inspector.get_check_constraints("read_snapshots")
    ):
        raise RuntimeError("Unexpected existing read_snapshots constraints")
    indexes = inspector.get_indexes("read_snapshots")
    if len(indexes) > 1 or any(
        index["name"] != "idx_read_snapshot_due"
        or index["column_names"] != ["kind", "schema_version", "refresh_at"]
        or index["unique"]
        or index.get("column_sorting")
        for index in indexes
    ):
        raise RuntimeError("Unexpected existing read_snapshots refresh index")
    return bool(indexes)


def upgrade() -> None:
    # Retry interrupted DDL after CREATE TABLE or CREATE INDEX, including a
    # process loss before Alembic writes its revision.
    columns = _columns()
    inspector = sa.inspect(op.get_bind())
    index_exists = False
    if inspector.has_table("read_snapshots"):
        index_exists = _validate_existing(inspector, columns)
    else:
        op.create_table("read_snapshots", *columns)
    if not index_exists:
        op.create_index("idx_read_snapshot_due", "read_snapshots", ["kind", "schema_version", "refresh_at"])


def downgrade() -> None:
    # DROP TABLE removes its own index. A completed drop followed by a lost
    # revision write is safe to retry; no other table participates.
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("read_snapshots"):
        _validate_existing(inspector, _columns())
        op.drop_table("read_snapshots")
