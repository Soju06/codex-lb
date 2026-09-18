## Context

Alembic owns revision selection, execution, version bookkeeping and transactions. Its completion callback alone cannot report a revision before it begins or identify a failed revision.

## Decisions

Wrap the Alembic migration-step iterator in the online environment. Yield each original step unchanged so Alembic retains execution and transaction control. Keep this compatibility adapter in one small module. Measure monotonic elapsed time around each yielded step. A surrounding exception handler reports the active revision and rethrows the original exception.

Use INFO for start and executed events, ERROR for failure. The CLI configures logging only when invoked as a command. No global logging setup during imports. Completed execution can still be rolled back by a later failure; use `executed`, not `committed`.

## Risks

The iterator integration depends on Alembic's migration callback. Real runner tests must protect compatibility with the pinned Alembic version, including multi-revision execution, failure and no-op upgrades.

## Non-goals

No background work, resumability framework, affected-row estimates, SQL logging, periodic heartbeat, or benchmark threshold.
