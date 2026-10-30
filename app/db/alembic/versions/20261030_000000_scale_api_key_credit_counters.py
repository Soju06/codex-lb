"""Preserve API-key credit fractions without changing public whole-credit units.

Pause every application writer for upgrade/downgrade: old processes use whole
credits in these columns. Downgrade discards fractions accumulated afterward.
"""

from alembic import op
from sqlalchemy import Column, String, inspect, text
from sqlalchemy.engine import Connection

revision = "20261030_000000_scale_api_key_credit_counters"
down_revision = "20260918_000000_merge_scim_and_overflow_heads"
branch_labels = None
depends_on = None

# Historical storage contract: do not import mutable application constants.
_SCALE = 100_000_000
_MAX_WHOLE = (2**63 - 1) // _SCALE
_COUNTERS = (
    ("api_key_limits", ("max_value", "current_value")),
    ("api_key_usage_reservation_items", ("reserved_delta", "actual_delta")),
)


def _has_credit_tier(connection: Connection) -> bool:
    return any(
        column["name"] == "credit_service_tier"
        for column in inspect(connection).get_columns("api_key_usage_reservations")
    )


def upgrade() -> None:
    connection = op.get_bind()
    if _has_credit_tier(connection):
        # Bootstrap/rewound-ledger replay already has the current storage units.
        return
    # Check every table before any conversion; SQLite multiplication otherwise
    # promotes overflowing integers to imprecise REAL values.
    for table, columns in _COUNTERS:
        unsafe = " OR ".join(f"{column} < 0 OR {column} > {_MAX_WHOLE}" for column in columns)
        query = text(f"SELECT 1 FROM {table} WHERE limit_type = 'credits' AND ({unsafe}) LIMIT 1")
        if connection.execute(query).first():
            raise RuntimeError("API-key credit counters exceed the fixed-point storage range")
    for table, columns in _COUNTERS:
        assignments = ", ".join(f"{column} = {column} * {_SCALE}" for column in columns)
        connection.execute(text(f"UPDATE {table} SET {assignments} WHERE limit_type = 'credits'"))
    # Write the replay fence last, inside the DML transaction (also on SQLite).
    op.add_column("api_key_usage_reservations", Column("credit_service_tier", String(), nullable=True))


def downgrade() -> None:
    connection = op.get_bind()
    if not _has_credit_tier(connection):
        return
    for table, columns in _COUNTERS:
        assignments = ", ".join(f"{column} = CAST({column} / {_SCALE} AS BIGINT)" for column in columns)
        connection.execute(text(f"UPDATE {table} SET {assignments} WHERE limit_type = 'credits'"))
    op.drop_column("api_key_usage_reservations", "credit_service_tier")
