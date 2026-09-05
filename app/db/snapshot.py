"""Cheap transient copies of loaded ORM rows for read-mostly hot paths.

The proxy selection path hands every request a private, mutable snapshot of
each ``Account`` and latest ``UsageHistory`` row so per-request runtime-state
sync never mutates cached or session-owned rows. Building those snapshots
through the mapped-class constructor pays per-attribute instrumentation
(event dispatch, change history) for every column; ``clone_row`` copies the
loaded column values straight between instance dicts instead, which is an
order of magnitude cheaper and yields the same attribute values.
"""

from __future__ import annotations

from typing import TypeVar

from sqlalchemy.orm.attributes import instance_dict
from sqlalchemy.orm.instrumentation import manager_of_class

_RowT = TypeVar("_RowT")


def clone_row(row: _RowT) -> _RowT:
    """Return a transient copy of ``row`` carrying its column attributes.

    Only mapped column attributes are copied (relationships and plain Python
    attributes are not). Values already present in the source instance dict
    are copied as-is; anything expired or never set falls back to ``getattr``
    so it resolves exactly like a direct attribute read on the source
    (a session refresh for expired persistent rows, ``None`` for unset
    transient attributes, ``DetachedInstanceError`` for expired detached rows).

    The clone gets a fresh ``InstanceState`` so instrumented writes
    (``clone.status = ...``) keep working. It is transient and must never be
    added to or merged into a session. Intentional delta versus constructing
    through ``__init__``: ``inspect(clone).modified`` is ``False`` and its
    ``committed_state`` is empty (the constructor records every column as a
    pending change), matching loaded-row semantics.
    """
    manager = manager_of_class(type(row))
    clone = manager.new_instance()
    source = instance_dict(row)
    target = instance_dict(clone)
    for attr in manager.mapper.column_attrs:
        key = attr.key
        target[key] = source[key] if key in source else getattr(row, key)
    return clone
