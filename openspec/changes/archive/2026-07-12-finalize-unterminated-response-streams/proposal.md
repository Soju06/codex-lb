# Finalize unterminated response streams

## Why

Large Codex Responses requests can bypass the HTTP bridge and use the raw HTTP
streaming path. That path receives HTTP 200 before the SSE body is complete, so
request logs must not treat the attempt as successful unless a terminal
Responses event was observed.

## What Changes

- Track terminal SSE events in the raw streaming Responses path.
- Classify upstream EOF before a terminal event as `stream_incomplete`, emit a
  synthetic `response.failed`, and record an upstream failure for account health.
- Classify downstream client cancellation before a terminal event as
  `client_disconnected` without penalizing the upstream account.
- Preserve normal `response.completed`, `response.failed`,
  `response.incomplete`, and `error` terminal handling.

## Impact

- **Spec**: `responses-api-compat`
- **Behavior**: raw HTTP streaming attempts no longer disappear as successful
  request-log rows when the body ends or is cancelled before a terminal event.
