## 1. Fleet freshness contract

- [x] 1.1 Carry the newest already-loaded standard usage `recorded_at` as non-serialized account-summary metadata.
- [x] 1.2 Add `usageRefreshedAt` to the fleet account schema and apply the existing usage-visibility redaction while preserving `lastRefreshAt`.

## 2. Regression coverage

- [x] 2.1 Cover newest-sample selection, absent samples, response shape, usage redaction, and separation from OAuth refresh.
- [x] 2.2 Prove a successful Force Probe usage write advances `usageRefreshedAt` without changing `lastRefreshAt`.
- [x] 2.3 Prove a successful `POST /api/fleet/refresh` usage write advances `usageRefreshedAt` without changing `lastRefreshAt`.

## 3. Validation

- [x] 3.1 Run the focused mapper, fleet-summary, and probe regressions plus affected lint, type, format, and diff checks.
- [x] 3.2 Run strict scoped OpenSpec validation and verify implementation/spec/task coherence.
