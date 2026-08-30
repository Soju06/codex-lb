## 1. Regression

- [x] 1.1 Add a deferred-promise `useOauth` test that starts flow A, awaits poll A, resets and starts flow B, then resolves A as success without completing OAuth, mutating B, or invalidating caches
- [x] 1.2 Add the matching stale-error case so A's error cannot land on B
- [x] 1.3 Run the focused hook tests and confirm the stale-success case fails on the unfenced hook

## 2. Fence

- [x] 2.1 Add a monotonic generation incremented by `reset` and `start`
- [x] 2.2 Capture flow ID and completion credentials before poll awaits, and ignore the result when generation or flow identity no longer match after the status await and after the completion await
- [x] 2.3 Keep current-flow success, error, and pending poll behavior unchanged
- [x] 2.4 Ignore stale `startOauth` success and error continuations after reset/restart

## 3. Verify

- [x] 3.1 Re-run focused and complete `use-oauth` tests until they pass without sleeps
- [x] 3.2 Run ESLint on changed files, frontend typecheck, and frontend build
- [x] 3.3 Prove the visible OAuth dialog keeps flow B pending after a mocked stale flow-A success, including a committed OauthDialog + useOauth regression that starts A, resets/restarts B through the dialog, and ignores a late A success
- [x] 3.4 Validate the scoped OpenSpec change strictly
