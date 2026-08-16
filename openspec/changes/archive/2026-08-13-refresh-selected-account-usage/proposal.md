## Why

The background usage scheduler already chooses one account per staggered slot, but it still reads latest usage for the full fleet and can re-evaluate unrelated accounts after that one refresh. On installations with many accounts and a large `usage_history` table, this multiplies the recurring database work identified in #708 even though only one credential slot can produce new usage in that tick.

## What Changes

- Scope each scheduler tick's before/after usage lookups to the selected account.
- Scope post-refresh warm-up and recoverable-status evaluation to that same account while retaining the fleet roster only where deterministic rotation or warm-up phase calculation requires it.
- Preserve the selected account's credential ownership, existing per-account failure handling, cache invalidation cadence, and the rule that database sessions close before upstream network I/O.
- Add regressions at the scheduler and repository-backed product path proving unrelated accounts are neither fetched nor mutated.
- Keep usage-write transaction batching, retention, rollups, and dashboard query optimization outside this change so the PR is self-contained.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `usage-refresh-policy`: Require every staggered background scheduler tick to read and evaluate usage only for its selected account while preserving account ownership and rotation semantics.

## Impact

- Affected code: `app/core/usage/refresh_scheduler.py`, its latest-usage repository protocol, and the limit-warm-up service's candidate/cohort input.
- Affected tests: scheduler/recovery unit tests and repository-backed usage-refresh integration coverage.
- No API, schema, migration, setting, dependency, dashboard, or deployment change.
