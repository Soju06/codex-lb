## Why

Additional-usage-gated models currently mix two identifier authorities: routing uses a local `model -> limit_name` map while persistence stores upstream `additional_rate_limits[].limit_name` verbatim. When upstream renames a quota limit, the router can no longer find fresh persisted rows even though usage refresh is healthy and eligible accounts exist. The system needs one internal canonical quota key that routing, persistence, aggregation, and UI can share while still preserving raw upstream names for observability.

## What Changes

- Add a canonical additional-quota registry that resolves model IDs and upstream aliases to one internal `quota_key`, plus user-facing labels. The registry MUST be config-backed so future routed quotas can be added without source-code edits.
- Support config-backed registry updates through explicit reload/restart semantics instead of implicit file-watch hot reload.
- Persist the internal `quota_key` alongside raw upstream `limit_name` / `metered_feature` values when additional usage rows are written.
- Make selection for explicitly mapped gated models use fresh persisted `additional_usage_history` snapshots addressed by canonical `quota_key` before building candidate runtime states.
- Fail closed with stable proxy error codes when a mapped model has no fresh quota data or no eligible accounts.
- Keep additional-quota eligibility checks from mutating persisted account status or changing `AccountState` semantics outside normal runtime bookkeeping.
- Render mapped human-readable additional quota labels from canonical quota metadata on the Accounts page.

## Capabilities

### New Capabilities

<!-- None. -->

### Modified Capabilities
- `query-caching`: account selection for gated additional-usage models must use fresh persisted canonical `quota_key` snapshots without mutating shared selection state.
- `responses-api-compat`: proxy failures for gated-model selection must expose stable error codes.
- `frontend-architecture`: the Accounts page must render mapped labels for additional quota limits.

## Impact

- Code: `app/modules/proxy/load_balancer.py`, `app/modules/proxy/service.py`, `app/modules/usage/repository.py`, `app/modules/usage/updater.py`, DB models/migrations, frontend account usage components, and the canonical quota registry loader/config.
- Tests: load balancer unit tests, repository/updater coverage for canonicalization and backfill, proxy selection integration coverage, frontend component/integration tests.
- Specs: `openspec/specs/query-caching/spec.md`, `openspec/specs/responses-api-compat/spec.md`, `openspec/specs/frontend-architecture/spec.md`.
