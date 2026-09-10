## 1. Regression

- [x] 1.1 Add a real `OAuthCallbackServer` integration test with distinct authorization-code and state sentinels.
- [x] 1.2 Run the focused test against the default runner and capture the intended secret-absence assertion failing.

## 2. Callback boundary

- [x] 2.1 Suppress access logging on the callback-only `aiohttp` runner without changing global logging.
- [x] 2.2 Re-run the same focused integration proof to green while preserving successful callback behavior.

## 3. Verification

- [x] 3.1 Run changed-file diagnostics, focused OAuth tests, lint, type checks, and the appropriate package build.
- [x] 3.2 Validate and verify the scoped OpenSpec change strictly.
- [x] 3.3 Run the real callback server under text and JSON logging and prove both sentinels stay absent.
