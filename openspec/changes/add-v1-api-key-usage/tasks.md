## 1. Specs

- [ ] 1.1 Add an API-key self-usage requirement for `GET /v1/usage`.
- [ ] 1.2 Validate OpenSpec changes.

## 2. Tests

- [ ] 2.1 Add integration coverage for missing/invalid API keys.
- [ ] 2.2 Add integration coverage for zero-usage responses, per-key usage scoping, and returned limit state.
- [ ] 2.3 Add integration coverage that `GET /v1/usage` still works when global proxy API-key auth is disabled.

## 3. Implementation

- [ ] 3.1 Add self-usage API-key validation that always requires a valid Bearer key.
- [ ] 3.2 Add `GET /v1/usage` and return usage totals plus current limits for the authenticated key.
- [ ] 3.3 Reuse API-key repository/service aggregation instead of scanning all keys.
