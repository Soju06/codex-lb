## Why

Native HTTP request logs populate queue and first-content timing but leave the existing first-upstream-event and response-created fields empty. Historical long waits therefore cannot be separated into pre-event delay and event-to-content delay using these rows.

## What Changes

- Populate the existing HTTP first-upstream-event and response-created timings with the current attempt clock.
- Preserve admission/TTFT origins and lazy/verbatim SSE forwarding.
- Persist the bounded architecture investigation and its attribution limits in OpenSpec context.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `proxy-runtime-observability`: Existing phase timings for HTTP attempts, including missing-event null semantics.

## Impact

HTTP streaming attempt and request-log arguments; existing fields only. No schema, new metric family, client-facing timing or telemetry framework. This scope is independent of preparation optimization.

Partial investigation follow-up for issue #2029; this scope does not independently close the broad performance issue.
