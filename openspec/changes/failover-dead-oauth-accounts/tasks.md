## 1. Account health

- [x] 1.1 Classify `token_revoked` as a permanent reauthentication failure.
- [x] 1.2 Preserve canonical HTTP 401 mapping for the upstream error spelling.

## 2. Compact recovery

- [x] 2.1 Exclude a dead account after a permanent post-401 refresh failure and
  retry movable compact work on another eligible account.
- [x] 2.2 Keep account-owned compact work fail-closed.
- [x] 2.3 Preserve API-key settlement before deferred account-health mutation.

## 3. Validation

- [x] 3.1 Run focused unit and integration regressions.
- [x] 3.2 Run lint, type checks, and strict OpenSpec validation.
