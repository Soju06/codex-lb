## Overview

This change closes two gaps where runtime behavior drifted from the intended contract:

1. Streaming Responses attempts already compute a remaining request budget, but only pass that budget to the upstream connect timeout override. The idle and total stream timeouts must be clamped to the same remaining budget on every attempt.
2. The `quota_key` backfill migration must resolve aliases through the configured additional quota registry so migrated rows match the keys used by runtime routing.

## Decisions

### Use a single helper for per-attempt stream timeout overrides

`ProxyService` now applies stream attempt overrides through one helper that sets connect, idle, and total timeout overrides together. This removes the duplicated connect-only wiring in the initial attempt and forced-refresh retry path and makes future regressions less likely.

### Reuse runtime canonicalization for migration backfill

The migration delegates quota-key backfill to `canonicalize_additional_quota_key(...)` and keeps the existing `"unknown"` fallback for rows that still cannot be resolved. This guarantees the migration uses the same registry file, alias set, and canonical key that runtime routing uses.

## Verification

- unit coverage proves the remaining budget is forwarded to connect, idle, and total overrides on the initial stream attempt
- unit coverage proves the forced-refresh retry path reapplies all three overrides with the recomputed remaining budget
- migration coverage proves a custom registry file backfills historical rows under the configured canonical key
