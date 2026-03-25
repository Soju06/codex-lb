"""add persisted cost to request_logs

Revision ID: 20260325_000000_add_request_log_cost
Revises: 20260321_210000_merge_request_log_tiers_and_dashboard_index_heads
Create Date: 2026-03-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

from app.core.usage.pricing import UsageTokens, calculate_cost_from_usage, get_pricing_for_model

# revision identifiers, used by Alembic.
revision = "20260325_000000_add_request_log_cost"
down_revision = "20260321_210000_merge_request_log_tiers_and_dashboard_index_heads"
branch_labels = None
depends_on = None

_BACKFILL_BATCH_SIZE = 1000
_TEMP_COST_TABLE_NAME = "request_log_cost_backfill"


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def _calculate_cost(
    *,
    model: str | None,
    service_tier: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
    cached_input_tokens: int | None,
    reasoning_tokens: int | None,
) -> float | None:
    if not model or input_tokens is None:
        return None
    resolved_output_tokens = output_tokens if output_tokens is not None else reasoning_tokens
    if resolved_output_tokens is None:
        return None
    resolved = get_pricing_for_model(model, None, None)
    if resolved is None:
        return None
    _, price = resolved
    normalized_cached_tokens = max(0, min(int(cached_input_tokens or 0), int(input_tokens)))
    return calculate_cost_from_usage(
        UsageTokens(
            input_tokens=float(input_tokens),
            output_tokens=float(resolved_output_tokens),
            cached_input_tokens=float(normalized_cached_tokens),
        ),
        price,
        service_tier=service_tier,
    )


def _create_backfill_temp_table(bind: Connection) -> None:
    bind.execute(sa.text(f"DROP TABLE IF EXISTS {_TEMP_COST_TABLE_NAME}"))
    bind.execute(
        sa.text(
            f"""
            CREATE TEMPORARY TABLE {_TEMP_COST_TABLE_NAME} (
                id INTEGER PRIMARY KEY,
                cost_usd FLOAT NULL
            )
            """
        )
    )


def _apply_cost_backfill_batch(
    bind: Connection,
    batch: Sequence[dict[str, int | float | None]],
) -> None:
    if not batch:
        return

    bind.execute(sa.text(f"DELETE FROM {_TEMP_COST_TABLE_NAME}"))
    bind.execute(
        sa.text(f"INSERT INTO {_TEMP_COST_TABLE_NAME} (id, cost_usd) VALUES (:id, :cost_usd)"),
        list(batch),
    )
    if bind.dialect.name == "sqlite":
        bind.execute(
            sa.text(
                f"""
                UPDATE request_logs
                SET cost_usd = (
                    SELECT tmp.cost_usd
                    FROM {_TEMP_COST_TABLE_NAME} AS tmp
                    WHERE tmp.id = request_logs.id
                )
                WHERE id IN (SELECT id FROM {_TEMP_COST_TABLE_NAME})
                """
            )
        )
        return

    bind.execute(
        sa.text(
            f"""
            UPDATE request_logs
            SET cost_usd = tmp.cost_usd
            FROM {_TEMP_COST_TABLE_NAME} AS tmp
            WHERE request_logs.id = tmp.id
            """
        )
    )


def upgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "request_logs")
    if not columns:
        return

    if "cost_usd" not in columns:
        with op.batch_alter_table("request_logs") as batch_op:
            batch_op.add_column(sa.Column("cost_usd", sa.Float(), nullable=True))

    request_logs = sa.table(
        "request_logs",
        sa.column("id", sa.Integer()),
        sa.column("model", sa.String()),
        sa.column("service_tier", sa.String()),
        sa.column("input_tokens", sa.Integer()),
        sa.column("output_tokens", sa.Integer()),
        sa.column("cached_input_tokens", sa.Integer()),
        sa.column("reasoning_tokens", sa.Integer()),
        sa.column("cost_usd", sa.Float()),
    )

    last_seen_id = 0
    _create_backfill_temp_table(bind)
    try:
        while True:
            rows = (
                bind.execute(
                    sa.select(
                        request_logs.c.id,
                        request_logs.c.model,
                        request_logs.c.service_tier,
                        request_logs.c.input_tokens,
                        request_logs.c.output_tokens,
                        request_logs.c.cached_input_tokens,
                        request_logs.c.reasoning_tokens,
                    )
                    .where(request_logs.c.id > last_seen_id)
                    .order_by(request_logs.c.id)
                    .limit(_BACKFILL_BATCH_SIZE)
                )
                .mappings()
                .all()
            )
            if not rows:
                break

            batch = [
                {
                    "id": row["id"],
                    "cost_usd": _calculate_cost(
                        model=row["model"],
                        service_tier=row["service_tier"],
                        input_tokens=row["input_tokens"],
                        output_tokens=row["output_tokens"],
                        cached_input_tokens=row["cached_input_tokens"],
                        reasoning_tokens=row["reasoning_tokens"],
                    ),
                }
                for row in rows
            ]
            _apply_cost_backfill_batch(bind, batch)
            last_seen_id = int(rows[-1]["id"])
    finally:
        bind.execute(sa.text(f"DROP TABLE IF EXISTS {_TEMP_COST_TABLE_NAME}"))


def downgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "request_logs")
    if not columns or "cost_usd" not in columns:
        return

    with op.batch_alter_table("request_logs") as batch_op:
        batch_op.drop_column("cost_usd")
