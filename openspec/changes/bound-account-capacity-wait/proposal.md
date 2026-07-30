# Bound HTTP bridge account-capacity waits

## Why

Requests that exhaust an account concurrency cap enter a recoverable capacity wait bounded only by the HTTP bridge request budget, which defaults to 7200 seconds. A live deployment showed requests parked in the retry loop for over eight minutes, after which clients timed out without receiving the existing HTTP 429 cap envelope. Because these failures happen before submission, they were also absent from request logs.

## What Changes

- Bound recoverable HTTP bridge account-capacity waits by a fixed 120-second per-request ceiling in addition to the bridge request budget.
- Preserve the first local-cap error and deadline across re-prepared request states and subsequent recoverable errors.
- Keep `response_create_gate_timeout` waits bounded only by the bridge request budget.
- Surface and record the original `account_stream_cap` or `account_response_create_cap` failure when the ceiling expires.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `proxy-admission-control`: Define a bounded account-capacity wait for bridged Responses work and terminal request-log visibility.

## Impact

- `app/modules/proxy/_service/http_bridge/streaming.py`: bounded capacity waits, original-cap error propagation, and terminal request logging.
- `app/modules/proxy/_service/support.py`: per-request capacity deadline and original error state.
- No new setting, database migration, dashboard, or API schema change. Existing error envelopes and reason codes are reused.
