## Overview

This change closes two gaps where runtime behavior drifted from the intended contract:

1. Streaming Responses attempts already compute a remaining request budget, but only pass that budget to the upstream connect timeout override. The idle and total stream timeouts must be clamped to the same remaining budget on every attempt.
2. The `quota_key` backfill migration must stay deterministic across environments, while runtime canonicalization must normalize configured keys before persisting or querying them.

## Decisions

### Use a single helper for per-attempt stream timeout overrides

`ProxyService` now applies stream attempt overrides through one helper that sets connect, idle, and total timeout overrides together. This removes the duplicated connect-only wiring in the initial attempt and forced-refresh retry path and makes future regressions less likely.

### Normalize configured quota keys at registry load

`AdditionalQuotaDefinition` now stores a normalized canonical key instead of the raw configured `quota_key`. That keeps model lookup, alias resolution, persistence, and delete/read filters on the same identifier even when operators spell the configured key with mixed case or punctuation.

### Keep migration backfill self-contained and versioned

The migration no longer imports runtime registry resolution. Instead, it carries a revision-local alias snapshot for the known additional quota families and falls back to normalized raw identifiers when no versioned alias matches. This keeps the backfill reproducible for the lifetime of the revision and avoids environment-specific durable data.

## Verification

- unit coverage proves the remaining budget is forwarded to connect, idle, and total overrides on the initial stream attempt
- unit coverage proves the forced-refresh retry path reapplies all three overrides with the recomputed remaining budget
- unit coverage proves mixed-case configured quota keys are normalized before runtime mapping and persistence
- migration coverage proves backfill remains pinned to the revision-local alias snapshot even when the runtime registry is overridden
