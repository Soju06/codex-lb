## Why

A real quota-window reset can leave a `rate_limited` account unavailable until a stale persisted cooldown deadline, even though fresh usage already proves that the blocked window reset and quota is available. This also prevents the normal reset warm-up from starting the new window, so recovery must precede warm-up without weakening generic 429 and Retry-After protection.

## What Changes

- Allow background usage refresh to recover a `rate_limited` account before its persisted `reset_at` only when a post-block monthly-window transition proves the specific blocked window reset, the new window has available quota, and the minimum 30-second post-429 floor has elapsed.
- Require recovery to use a compare-and-set transition that matches the blocked status and markers, clears `reset_at` and `blocked_at`, and completes before normal warm-up evaluation.
- Keep generic 429 and Retry-After cooldowns protected when no qualifying reset transition exists, and keep exhausted or unsafe account states blocked.
- Restore warm-up traffic to `active` accounts only while triggering one deduplicated warm-up after every confirmed selected-window reset, regardless of the previous window's usage percentage.
- Add regression coverage for restart-persisted reset evidence, stale or mismatched markers, concurrent re-blocking, exhausted fresh windows, the cooldown floor, unsafe states, and reset-tuple deduplication.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `usage-refresh-policy`: Define recovery-before-warm-up behavior for a reset-confirmed monthly window and require warm-up after every real selected-window reset rather than only exhausted-to-available transitions.
- `account-routing`: Permit the strict reset-confirmed recovery exception to a future persisted cooldown while preserving cross-replica enforcement for generic 429 and Retry-After cooldowns.

## Impact

- Affected code: background usage refresh scheduling, recoverable-status reconciliation, limit warm-up candidate construction, and the compare-and-set account-status repository path.
- Affected tests: focused scheduler, status-recovery, warm-up, and repository-backed integration coverage.
- No API, schema, migration, setting, dependency, dashboard, or deployment contract changes.
