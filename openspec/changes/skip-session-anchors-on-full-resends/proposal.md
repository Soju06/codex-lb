## Why

An HTTP Responses full resend can match a live bridge session's stored input prefix and receive that session's `previous_response_id`. The proxy then trims a verified self-contained request even though it already carries its own continuity, and a stale upstream response ID can make every retry fail.

## What Changes

- Do not inject a session-level `previous_response_id` into a full resend verified to retain prior completed output or exactly settle the session's pending direct tool calls.
- Forward that verified self-contained input without session-anchor-based prefix trimming.
- Keep the existing anchor and trim behavior when the resend does not prove that context.
- Preserve session-anchor injection for compatible non-full-resend continuations.
- Add bridge-level regression coverage for both paths.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `responses-api-compat`: Full resends remain self-contained when a reusable HTTP bridge session already has a completed response anchor.

## Impact

- Affected code: HTTP Responses bridge session-anchor selection in `app/modules/proxy/_service/http_bridge/streaming.py`.
- Affected tests: focused HTTP bridge unit coverage.
- No endpoint, schema, setting, dependency, account-selection, or database change.
