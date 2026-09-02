"""Persistent ownership markers for additive HTTP bridge migrations.

Alembic imports each revision in a fresh process during a normal downgrade.
Revision-local bookkeeping therefore cannot determine which objects an
upgrade created.  This small table records that ownership in the database so
each downgrade can remove only objects created by its own revision.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

OWNERSHIP_TABLE = "http_bridge_migration_object_ownership"


def _ownership_table() -> sa.TableClause:
    return sa.table(
        OWNERSHIP_TABLE,
        sa.column("revision", sa.String(128)),
        sa.column("object_type", sa.String(32)),
        sa.column("object_name", sa.String(128)),
    )


def ensure_ownership_table(bind) -> None:
    if sa.inspect(bind).has_table(OWNERSHIP_TABLE):
        return
    op.create_table(
        OWNERSHIP_TABLE,
        sa.Column("revision", sa.String(128), nullable=False),
        sa.Column("object_type", sa.String(32), nullable=False),
        sa.Column("object_name", sa.String(128), nullable=False),
        sa.PrimaryKeyConstraint("revision", "object_type", "object_name"),
    )


def mark_created(bind, revision: str, object_type: str, object_name: str) -> None:
    """Record ownership idempotently after an additive object is created."""

    ensure_ownership_table(bind)
    table = _ownership_table()
    marker = dict(revision=revision, object_type=object_type, object_name=object_name)
    if bind.execute(
        sa.select(table.c.revision).where(*(table.c[key] == value for key, value in marker.items()))
    ).first():
        return
    bind.execute(sa.insert(table).values(**marker))


def was_created(bind, revision: str, object_type: str, object_name: str) -> bool:
    if not sa.inspect(bind).has_table(OWNERSHIP_TABLE):
        return False
    table = _ownership_table()
    return (
        bind.execute(
            sa.select(table.c.revision).where(
                table.c.revision == revision,
                table.c.object_type == object_type,
                table.c.object_name == object_name,
            )
        ).first()
        is not None
    )


def forget_created(bind, revision: str, object_type: str, object_name: str) -> None:
    if not sa.inspect(bind).has_table(OWNERSHIP_TABLE):
        return
    table = _ownership_table()
    bind.execute(
        sa.delete(table).where(
            table.c.revision == revision,
            table.c.object_type == object_type,
            table.c.object_name == object_name,
        )
    )


def drop_ownership_table_if_empty(bind) -> None:
    if not sa.inspect(bind).has_table(OWNERSHIP_TABLE):
        return
    table = _ownership_table()
    if bind.execute(sa.select(sa.func.count()).select_from(table)).scalar_one() == 0:
        op.drop_table(OWNERSHIP_TABLE)
