## Context

The persistent native helper already emits a low-cardinality `failure_phase`
for WebSocket errors. `NativeUpstreamWebSocket.receive()` currently maps the
exception to a credential-safe public message and returns only `error_code`.
The WebSocket relay then defaults the request-log failure to
`stream_incomplete`, leaving the existing `failure_phase` and `failure_detail`
columns null. The separate application-message queue remains intentionally
bounded at 64 and can fail with `consumer_backpressure` when a relay stops
draining it.

## Decisions

### Carry metadata alongside the public message

Add optional `failure_phase` and `failure_detail` fields to the internal
`UpstreamWebSocketMessage` and `UpstreamWebSocketTransportError` contracts.
The public `error` text remains unchanged. Native details are normalized to a
bounded, low-cardinality value such as `native_websocket_phase=transport`;
queue overflow additionally records `message_queue_depth` and
`message_queue_limit`.

### Reuse request-log fields

The relay copies metadata to each request state immediately before terminal
failure finalization, after any safe pre-created replay decision. This avoids
stamping a later successful replay with metadata from a failed socket and
avoids a schema migration. Existing request-state overrides remain authoritative
for continuity-specific failures.

### Keep failure policy unchanged

Metadata is observational only. The existing `error_code`, account-neutral
classification, retry refusal, and account-health behavior remain unchanged.
Queue depth is logged only when the bounded application-message queue actually
overflows, preventing high-volume per-frame logging.

## Privacy and bounds

Only the native phase and fixed diagnostic keys are persisted. No raw native
exception, URL, header, payload, account email, API key, session id, or response
id is included. Queue depth and limit are integers bounded by the local queue
configuration.

## Rollback

Rollback removes the additive metadata and warning while preserving the current
queue behavior and downstream error contract. No data migration is required.
