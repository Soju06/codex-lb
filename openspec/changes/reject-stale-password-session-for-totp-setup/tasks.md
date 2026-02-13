## 1. TOTP Setup Session Guard

- [x] 1.1 Require both active password mode (`password_hash` exists) and `pw=true` session for TOTP setup start/confirm
- [x] 1.2 Keep error contract as 401 `authentication_required` when the guard fails

## 2. Regression Tests

- [x] 2.1 Add integration test for stale password-session cookie replay after password removal
- [x] 2.2 Assert both `/totp/setup/start` and `/totp/setup/confirm` are blocked in that scenario
