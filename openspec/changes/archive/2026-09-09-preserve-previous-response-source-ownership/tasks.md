## 1. Contract

- [x] 1.1 Sync the owner-evidence routing requirement into the main Responses compatibility spec.

## 2. Routing Implementation

- [x] 2.1 Remove response-ID-shape inference from the shared structural source-route exclusion policy.
- [x] 2.2 Make both HTTP Responses routes let recorded subscription ownership veto an otherwise valid model-source candidate.
- [x] 2.3 Apply the same owner-aware decision before direct WebSocket connect and reuse source guards.

## 3. Regression Coverage

- [x] 3.1 Update unit coverage for structural source-route exclusions without response-ID syntax classification.
- [x] 3.2 Cover subscription-owned and canonical source-owned prior responses on both HTTP Responses routes.
- [x] 3.3 Add direct WebSocket regressions for subscription-owner routing and canonical source-owner HTTP fallback.

## 4. Verification

- [x] 4.1 Run focused tests, Ruff, ty, and scoped/strict OpenSpec validation; inspect the final diff and worktree status.
