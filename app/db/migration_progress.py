from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from time import monotonic

from alembic.runtime.migration import MigrationContext, RevisionStep

logger = logging.getLogger(__name__)


@contextmanager
def revision_progress(context: MigrationContext) -> Iterator[None]:
    """Report online revision execution while Alembic retains transaction control."""
    # Alembic exposes only an after-apply callback. Keep the iterator adapter
    # here so starts and failures can be reported without replacing its runner.
    original = context._migrations_fn
    assert original is not None
    active: RevisionStep | None = None
    started = 0.0

    def steps(heads: tuple[str, ...], migration_context: MigrationContext) -> Iterator[RevisionStep]:
        nonlocal active, started
        for step in original(heads, migration_context):
            if not isinstance(step, RevisionStep):
                yield step
                continue
            active = step
            started = monotonic()
            direction = "upgrade" if step.is_upgrade else "downgrade"
            logger.info("Migration started revision=%s direction=%s", step.revision.revision, direction)
            yield step
            logger.info(
                "Migration executed revision=%s direction=%s elapsed_seconds=%.3f",
                step.revision.revision,
                direction,
                monotonic() - started,
            )
            active = None

    context._migrations_fn = steps
    try:
        yield
    except BaseException:
        if active is not None:
            logger.error(
                "Migration failed revision=%s direction=%s elapsed_seconds=%.3f",
                active.revision.revision,
                "upgrade" if active.is_upgrade else "downgrade",
                monotonic() - started,
            )
        raise
    finally:
        context._migrations_fn = original
