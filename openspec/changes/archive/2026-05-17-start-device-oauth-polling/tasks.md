## 1. Spec
- [x] 1.1 Add a frontend-architecture delta for robust Device Code OAuth polling.

## 2. Implementation
- [x] 2.1 Start device token polling from `_start_device_flow()` after state is saved.
- [x] 2.2 Keep `/api/oauth/complete` idempotent and duplicate-safe.

## 3. Validation
- [x] 3.1 Update the device OAuth integration test so it succeeds without calling `/api/oauth/complete`.
- [x] 3.2 Run the targeted OAuth integration tests.
- [x] 3.3 Run targeted lint and type checks for the changed files.
- [x] 3.4 Validate OpenSpec specs locally.
