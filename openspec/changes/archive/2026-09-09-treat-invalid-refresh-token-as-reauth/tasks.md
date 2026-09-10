## 1. Implementation

- [x] Classify `invalid_refresh_token` as a permanent OAuth refresh failure.
- [x] Map `invalid_refresh_token` to `AccountStatus.REAUTH_REQUIRED`.
- [x] Cover the guarded refresh-account path for the upstream code.

## 2. Validation

- [x] Run focused auth-refresh tests.
- [x] Run strict OpenSpec validation for this change.
