## Context

Account usage is provider-sampled; selection already normalizes weekly-only accounts and expires past reset timestamps. Existing sockets can admit requests without selecting again.

## Goals / Non-Goals

Reserve quota without mutating provider percentages or account status. Do not cancel already admitted work, add monthly/additional-quota caps, or introduce new dependencies.

## Decisions

Store two nullable percentages on accounts and replace them atomically through a dashboard-write-protected endpoint. Null disables each cap. Derive cap eligibility from standard usage, independently of model-specific quota bypasses. Preserve account ownership even when capped. Reuse checks use an in-memory snapshot refreshed after usage/config changes and through existing cross-replica invalidation; no per-request database lookup is added to bridge reuse.

## Risks / Trade-offs

- Sampled usage and already admitted concurrent work can overshoot a cap; enforcement starts with the next observed usage update, not an upstream hard quota.
- Missing windows do not block; expired windows no longer block. A weekly primary slot is weekly, never 5h. Monthly quotas are outside scope.
- Snapshot updates follow existing cache freshness rather than distributed transactional admission.

## Migration Plan

Add nullable columns on the current Alembic head; existing accounts remain uncapped. Downgrade drops only those columns. Verify using a temporary database.
