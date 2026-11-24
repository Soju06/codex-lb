# Mark selector refusals before any upstream attempt

## Why
Selector-generated quota errors share their public error shape with upstream errors. Error strings cannot prove that no upstream attempt occurred.

## What Changes
Expose `X-Codex-LB-No-Upstream-Attempt: 1` only on raw HTTP Responses selector refusals with trusted request-local proof that no upstream attempt has begun. Keep existing statuses, JSON/SSE bodies, retries and account policies. Unproved bridge/forwarded paths and failures after an attempt remain unmarked. Absence is unknown; the marker authorizes no replay.

## Impact
Responses retry producer, HTTP error/stream rendering and existing response-path tests only. No configuration, runtime, scheduler, account or frontdoor change.
