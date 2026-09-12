"""Drift guards for the dashboard's subscription-overflow read (#2123 WP-G).

The dashboard aggregate spells the two ``request_logs.source`` values as
literals instead of importing ``app.modules.proxy.overflow`` -- the decision
module drags the load-balancer probe, the pin primitive and the dispatch owner
into the dashboard's import graph, and the positive ratchet in
``tests/unit/test_subscription_overflow_inert.py`` keeps it out. Tests import
freely, so this file is where the two spellings are pinned together, and where
the ``IN`` predicate is pinned against the prefix ``LIKE`` the design text
originally suggested (a prefix ``LIKE`` cannot use the plain btree
``idx_logs_source_requested_at`` under a non-C PostgreSQL collation, which would
put a sequential scan over ``request_logs`` into the 30 s dashboard poll).

The tuple is pinned against the whole enum ``tests/unit/_overflow_constants.py``
discovers, not against the two constant names that exist today. The ``IN``
predicate is closed, so a third ``source`` value absent from the tuple is spend
the overflow tile under-counts -- and installations the tile's existence probe
cannot see -- with nothing else on either side failing.
"""

from __future__ import annotations

from datetime import datetime

from app.modules.settings.subscription_overflow import (
    OVERFLOW_REQUEST_LOG_SOURCES,
    overflow_existence_statement,
    overflow_window_statement,
)
from tests.unit._overflow_constants import BACKEND_MODULE, SOURCE_PREFIX, discovery_error, overflow_constants

_SINCE = datetime(2026, 9, 1, 0, 0, 0)
_UNTIL = datetime(2026, 9, 8, 0, 0, 0)

_SOURCE_LITERALS = overflow_constants(SOURCE_PREFIX)


def test_dashboard_sources_are_exactly_the_dispatch_labels() -> None:
    """A ``source`` value absent from the closed ``IN`` tuple is spend the tile never sees."""
    error = discovery_error(SOURCE_PREFIX, _SOURCE_LITERALS)
    assert error is None, error

    dashboard = set(OVERFLOW_REQUEST_LOG_SOURCES)
    expected = set(_SOURCE_LITERALS.values())

    assert dashboard == expected, {
        "in_dashboard_only": sorted(dashboard - expected),
        "in_overflow_only": sorted(expected - dashboard),
    }
    assert len(dashboard) == len(OVERFLOW_REQUEST_LOG_SOURCES), (
        f"OVERFLOW_REQUEST_LOG_SOURCES repeats a value: {OVERFLOW_REQUEST_LOG_SOURCES}; "
        f"{BACKEND_MODULE} gives each dispatch kind its own `source`"
    )


def test_window_statement_matches_the_sources_by_equality_not_by_prefix() -> None:
    compiled = str(overflow_window_statement(since=_SINCE, until=_UNTIL).compile())

    assert "request_logs.source IN " in compiled
    assert "LIKE" not in compiled.upper()


def test_existence_statement_matches_the_sources_by_equality_not_by_prefix() -> None:
    compiled = str(overflow_existence_statement().compile())

    assert "request_logs.source IN " in compiled
    assert "LIKE" not in compiled.upper()
    assert "LIMIT" in compiled.upper()


def test_window_statement_carries_both_source_values_and_the_window_bounds() -> None:
    statement = overflow_window_statement(since=_SINCE, until=_UNTIL)
    rendered = str(statement.compile(compile_kwargs={"literal_binds": True}))

    for source in OVERFLOW_REQUEST_LOG_SOURCES:
        assert f"'{source}'" in rendered, source
    assert "request_logs.requested_at >=" in rendered
    assert "request_logs.requested_at <=" in rendered


def test_window_statement_excludes_soft_deleted_rows() -> None:
    compiled = str(overflow_window_statement(since=_SINCE, until=_UNTIL).compile())

    assert "request_logs.deleted_at IS NULL" in compiled
