## 1. Canonical quota-key registry and persistence

- [x] 1.1 Add a config-backed canonical additional-quota registry/helper that resolves model IDs plus upstream aliases to one internal `quota_key` and display label.
- [x] 1.2 Add persistence support for canonical `quota_key` on `additional_usage_history`, including migration/backfill and repository interfaces.
- [x] 1.3 Normalize incoming additional usage payload rows to canonical `quota_key` while preserving raw upstream `limit_name` / `metered_feature`.

## 2. Proxy selection and downstream consumers

- [x] 2.1 Pre-filter candidate accounts for mapped gated models using fresh persisted `additional_usage_history.quota_key` rows before building selection states.
- [x] 2.2 Fail closed for mapped gated models and propagate stable selection error codes through proxy responses.
- [x] 2.3 Preserve existing `AccountState` and persisted account-status semantics while evaluating gated-model eligibility.
- [x] 2.4 Update downstream aggregation/output paths (accounts/dashboard/proxy usage status) to read canonical `quota_key` while keeping label/raw metadata coherent.

## 3. UI and verification

- [x] 3.1 Render mapped additional quota labels from canonical quota metadata on the Accounts page.
- [x] 3.2 Update unit/integration/frontend coverage for alias drift, canonicalization, freshness, stable errors, and mapped labels.
- [x] 3.3 Run focused tests, local frontend integration/E2E coverage, and full project/spec verification for the touched paths.
