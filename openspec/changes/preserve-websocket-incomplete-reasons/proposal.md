# Preserve WebSocket incomplete-response reasons

## Why

Upstream Responses WebSocket streams can terminate with `response.incomplete`
and a machine-readable `incomplete_details.reason`. The proxy currently stores
these terminal requests as a generic `upstream_error` with no message, which
makes an upstream `max_output_tokens` limit indistinguishable from unrelated
failures in request logs.

## What Changes

- Preserve a valid upstream incomplete reason in the WebSocket request log.
- Record the reason as both the terminal error code and error message while
  retaining the existing non-penalizing handling of incomplete responses.

## Impact

- **Spec**: `responses-api-compat`
- **Behavior**: request logs for WebSocket `response.incomplete` events become
  diagnostically accurate; downstream event forwarding is unchanged.
