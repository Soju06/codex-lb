## Why

Additional-usage-gated models are keyed by upstream `limit_name`, but the current routing path still reasons about model IDs too late and without a single canonical mapping. That can send explicitly gated models through the normal account pool, mislabel quota data in the UI, and accidentally persist selection-side state mutations while eligibility is being evaluated.

## What Changes

- Add a canonical gated-model mapping that resolves model IDs to additional-usage `limit_name` keys and user-facing labels.
- Make selection for explicitly mapped gated models use fresh persisted `additional_usage_history` snapshots before building candidate runtime states.
- Fail closed with stable proxy error codes when a mapped model has no fresh quota data or no eligible accounts.
- Keep additional-quota eligibility checks from mutating persisted account status or changing `AccountState` semantics outside normal runtime bookkeeping.
- Render mapped human-readable additional quota labels on the Accounts page.

## Capabilities

### New Capabilities

<!-- None. -->

### Modified Capabilities
- `query-caching`: account selection for gated additional-usage models must use fresh persisted `limit_name` snapshots without mutating shared selection state.
- `responses-api-compat`: proxy failures for gated-model selection must expose stable error codes.
- `frontend-architecture`: the Accounts page must render mapped labels for additional quota limits.

## Impact

- Code: `app/modules/proxy/load_balancer.py`, `app/modules/proxy/service.py`, `app/modules/usage/repository.py`, frontend account usage components, and any shared gated-model registry/helper.
- Tests: load balancer unit tests, proxy selection integration coverage, frontend component tests.
- Specs: `openspec/specs/query-caching/spec.md`, `openspec/specs/responses-api-compat/spec.md`, `openspec/specs/frontend-architecture/spec.md`.
