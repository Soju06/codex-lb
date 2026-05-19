## 1. Spec
- [x] 1.1 Add an `api-keys` delta for the explicit proxy unauthenticated client CIDR allowlist.
- [x] 1.2 Update the main `api-keys` spec so the normative proxy-auth behavior matches the implementation.

## 2. Implementation
- [x] 2.1 Add a validated environment-backed proxy unauthenticated client CIDR setting.
- [x] 2.2 Allow only raw socket peers in that CIDR list to bypass proxy API key auth when dashboard API key auth is disabled.
- [x] 2.3 Keep dashboard bootstrap/auth behavior and global locality classification unchanged.

## 3. Validation
- [x] 3.1 Add regression tests for settings parsing, HTTP proxy auth, websocket proxy auth, and dashboard isolation.
- [x] 3.2 Run targeted pytest coverage for the new proxy auth path.
