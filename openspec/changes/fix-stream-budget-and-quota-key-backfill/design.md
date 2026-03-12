## Overview

This change closes two gaps where runtime behavior drifted from the intended contract:

1. Streaming Responses attempts already compute a remaining request budget, but only pass that budget to the upstream connect timeout override. The idle and total stream timeouts must be clamped to the same remaining budget on every attempt.
2. The `quota_key` backfill migration must align with the configured registry at upgrade time, while runtime canonicalization and reads must keep historical rows visible if canonical keys later diverge.
3. Additional-usage refresh can receive multiple upstream aliases for the same canonical quota in one payload, so refresh-time pruning must operate on the merged canonical quota state rather than item order.

## Decisions

### Use a single helper for per-attempt stream timeout overrides

`ProxyService` now applies stream attempt overrides through one helper that sets connect, idle, and total timeout overrides together. This removes the duplicated connect-only wiring in the initial attempt and forced-refresh retry path and makes future regressions less likely.

### Normalize configured quota keys at registry load

`AdditionalQuotaDefinition` now stores a normalized canonical key instead of the raw configured `quota_key`. That keeps model lookup, alias resolution, persistence, and delete/read filters on the same identifier even when operators spell the configured key with mixed case or punctuation.

### Keep migration backfill aligned with the configured registry

The migration resolves `quota_key` values from the configured registry file available at upgrade time instead of hard-coding the default Spark key. That ensures the first post-upgrade runtime sees the same canonical key that the deployment configured for routing.

### Read historical rows through raw alias compatibility

Repository lookups and deletes no longer rely only on the persisted `quota_key` column. When a request targets a known canonical quota, repository filters also match the raw upstream alias fields (`limit_name` / `metered_feature`) registered for that quota. This keeps previously persisted rows visible even if an operator later renames the canonical key or the migration and runtime use different registry files.

### Merge refresh aliases before deleting stale quota rows

The usage refresh path now folds `additional_rate_limits` into one `quota_key -> window` snapshot before it writes or deletes anything. Aliases with `rate_limit == null` no longer erase fresh rows written earlier in the same refresh, and split-window aliases can contribute different windows to the same canonical quota. When two aliases disagree on the same window payload, refresh keeps the higher `used_percent` sample so gating stays conservative and deterministic.

## Verification

- unit coverage proves the remaining budget is forwarded to connect, idle, and total overrides on the initial stream attempt
- unit coverage proves the forced-refresh retry path reapplies all three overrides with the recomputed remaining budget
- unit coverage proves mixed-case configured quota keys are normalized before runtime mapping and persistence
- migration coverage proves backfill follows the configured canonical key supplied by the deployment registry
- repository coverage proves raw alias compatibility keeps historical rows queryable and deletable after canonical key renames
- usage refresh coverage proves mixed aliases for the same canonical quota are merged before stale-row pruning runs
