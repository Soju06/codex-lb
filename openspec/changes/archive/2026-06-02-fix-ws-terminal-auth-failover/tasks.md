## 1. Implementation

- [x] 1.1 Add permanent account status reasons for terminal WebSocket session-ended and post-refresh auth invalidation.
- [x] 1.2 Detect pre-visible terminal WebSocket auth failures on direct Responses WebSocket handling.
- [x] 1.3 Retry generic terminal auth failures once with forced refresh on the same account.
- [x] 1.4 Mark/exclude re-auth-required or post-refresh-invalidated accounts and replay on another eligible account when possible.
- [x] 1.5 Keep visible-output and non-replayable continuation failures surfaced without replay.

## 2. Tests

- [x] 2.1 Add WebSocket integration coverage for session-ended auth failure failing over to another account.
- [x] 2.2 Add WebSocket integration coverage for generic auth failure retrying after forced refresh before failover.
- [x] 2.3 Add or preserve coverage that visible-output auth failures are not replayed.

## 3. Verification

- [x] 3.1 Run targeted WebSocket response tests.
- [x] 3.2 Run `openspec validate --specs`.
- [x] 3.3 Review diff and PR readiness.
