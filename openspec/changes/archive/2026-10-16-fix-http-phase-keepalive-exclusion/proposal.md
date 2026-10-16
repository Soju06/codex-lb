## Why

An HTTP SSE keepalive with `event: codex.keepalive` and an empty JSON object currently establishes the first upstream phase timestamp. The measured latency therefore reflects a heartbeat instead of the first response event.

## What Changes

- Exclude keepalives identified by the SSE event field as well as by the JSON payload from HTTP phase observations.
- Extend the existing HTTP route, persistence, and metric coverage with header-only keepalives.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `proxy-runtime-observability`: Make keepalive exclusion independent of whether its type is carried in the SSE event field or JSON payload.

## Impact

The HTTP phase tracker and its existing tests change. Downstream payload classification, forwarding, storage schema, settings, and metric families remain unchanged.
