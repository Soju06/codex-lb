# Tasks

## 1. Post-refresh transient terminal

- [x] 1.1 Render the last-account post-refresh terminal through
      `_response_failed_event_from_upstream_error`, keeping `resets_at` while
      preserving the existing code, message, and response id.

## 2. Failed pre-created bridge replay

- [x] 2.1 Preserve a status-bearing 429 quota terminal (code plus reset
      metadata) when the bounded pre-created replay was attempted and failed,
      clearing the sticky error overrides.
- [x] 2.2 Keep every other failed replay fail-closed as the synthetic 502
      `stream_incomplete`.

## 3. Spec + tests

- [x] 3.1 MODIFIED `responses-api-compat` requirements: failed-replay terminal
      preservation and post-refresh reset-metadata parity.
- [x] 3.2 Focused unit coverage for the post-refresh terminal: the event carries
      the upstream `resets_at`; the test fails against the pre-fix call.
- [x] 3.3 Focused unit coverage for the bridge: quota terminal preserved with
      reset metadata and no stale override, plus the non-quota fail-closed
      guard; both fail against their mutants.
- [x] 3.4 Updated the pre-existing masked-replay unit test to the new contract
      instead of leaving a stale expectation.
