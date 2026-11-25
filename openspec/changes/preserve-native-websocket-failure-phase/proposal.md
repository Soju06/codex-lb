## Why
Native WebSocket receive errors lose their failure phase at the relay adapter, making a truncated stream indistinguishable from consumer backpressure or helper failure.

## What Changes
- Preserve a bounded, credential-safe native failure phase through relay finalization into the existing request-log field.
- Preserve existing public error envelopes and any stronger request failure classification.
- Add adapter and actual WebSocket-route regression coverage.

## Impact
- Affected spec: proxy-runtime-observability
- No schema, routing, retry, queue-size, or configuration changes.
