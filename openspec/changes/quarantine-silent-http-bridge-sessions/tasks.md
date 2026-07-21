## 1. Specification

- [x] Define the bounded `response.created` wait and no-replay safety rule.
- [x] Define temporary direct-HTTP fallback with continuity account ownership.
- [x] Define cleanup when the downstream closes after the initial heartbeat.

## 2. Implementation

- [x] Track and bound the post-submit wait for `response.created`.
- [x] Retire, close, and quarantine silent bridge sessions.
- [x] Bypass quarantined bridge keys on the next independent client retry.
- [x] Close the bridge lifecycle iterator when the public stream closes.

## 3. Verification

- [x] Cover silent-session quarantine and direct-HTTP retry.
- [x] Cover `previous_response_id` account ownership during fallback.
- [x] Cover unrelated-account concurrency while one bridge is silent.
- [x] Cover downstream cancellation after the initial heartbeat.
- [x] Run focused and full bridge/API integration tests, lint, type checks, and
  OpenSpec validation.
