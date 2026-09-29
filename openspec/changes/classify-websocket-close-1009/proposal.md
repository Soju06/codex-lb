## Why

An upstream WebSocket close 1009 indicates a message-size rejection, but the
HTTP bridge and direct relay treat it as a transient disconnect. Retrying the
same request can exclude the only account and mask the cause with `no_accounts`.

## What Changes

- Classify surfaced close code 1009 as terminal `payload_too_large`.
- Do not replay, exclude/rotate accounts, or write account error-health for it.
- Preserve ordinary terminal cleanup and the existing HTTP/SSE/WebSocket envelopes.
- Leave other close codes, transport limits, image routing, settings and IPC unchanged.

## Capabilities

### Modified Capabilities

- `responses-api-compat`: a close-code-exact exception to transient failure handling.

## Impact

Small bridge/direct-relay fix extracted from #2534. It can land independently
of #2503/#2508 and the parser/Lite changes. No flag, migration, Rust or new dependency.
Related to #2465, but does not claim to resolve every issue in that report.
