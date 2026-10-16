# Tasks

## 1. Contract and persistence

- [x] 1.1 Add the nullable constrained `api_keys.usage_share_percent` column and forward migration.
- [x] 1.2 Thread `usageSharePercent` through API-key create, update, response, policy projection, and strict validation without changing existing keys.
- [x] 1.3 Add backend CRUD and policy-projection tests.

## 2. Estimation and admission

- [x] 2.1 Add one small pure estimator for normalized pool capacity, proportional per-account attribution, and the equality boundary.
- [x] 2.2 Add one watermark-aware aggregate demand query over the existing quarter rollup and raw tail, preserving unkeyed and warm-up traffic as unattributed denominator demand.
- [x] 2.3 Reuse existing account assignment and long-window normalization while rejecting stale monthly/weekly substitutions.
- [x] 2.4 Carry the estimate in existing `ApiKeyData`; do not add a second cache or ledger.
- [x] 2.5 Enforce once at the quota-consuming subscription request boundary: public HTTP origin or fresh direct-WebSocket turn; bypass reattach and non-subscription work.
- [x] 2.6 Fail open on incomplete/stale evidence and schedule the existing coalesced usage refresh.
- [x] 2.7 Add focused estimator, repository, policy-projection, equality, fail-open, reattach, bridge-origin, socket-reuse, and reconnect tests.

## 3. Dashboard

- [x] 3.1 Add the create/edit control, request/response schemas, summaries, mocks, and translations.
- [x] 3.2 Add focused frontend tests for create, edit, clear, validation, unrelated-edit preservation, table summary, and detail display.

## 4. Verification

- [x] 4.1 Run migration topology/drift checks, broader backend proxy/API-key suites, full ruff/format/ty, focused frontend behavior tests plus full frontend lint/typecheck/build, repository architecture/simplicity guards, and strict OpenSpec validation.
- [x] 4.2 Review the final diff for duplicated policy engines, unnecessary configuration, and safe LOC reductions.
- [x] 4.3 Merge the completed deltas into stable specs/context and archive the change.
