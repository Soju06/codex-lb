## Context

The schema-ahead guard already rejects an upgrade before Alembic execution. Its error mentions deploying a newer image or downgrading, but omits the separately requested hint from #1470.

## Goals / Non-Goals

Provide accurate conditional guidance through the existing migration CLI and startup error. Do not change stamping, locks, startup readiness, migration transactions or background data phases.

## Decisions

Extend the existing error text, keeping the check in place. The migration module is already large, but extracting a separate module for this single diagnostic would add an interface without a separate responsibility.

Use CLI subprocess tests against a disposable SQLite fixture to verify the accepted public command/error contract. Preserve fixture contents before and after the rejected commands. No live PostgreSQL or Docker access is required for a text-only change.

## Risks / Trade-offs

An unknown revision cannot be traversed by the old build's Alembic stamp command. Guidance must require the migration-capable build that contains both revisions. Do not introduce purge or automatically infer compatibility from additive DDL. Defaults, constraints and data readers can still be incompatible.

## Migration Plan

No database migration is introduced. Deploying or reverting this change only changes guidance.
