# Native Responses WebSocket event interpretation

## Boundary and legacy cleanup

The native helper now opts into interpretation only for the Responses WebSocket
policy. The Live WebSocket path and Python's aiohttp WebSocket implementation
remain opaque. Rust parses valid JSON objects, emits compact ASCII JSON text,
normalizes the three existing Responses aliases, and attaches `event_type` plus
`python_normalization`. Invalid JSON and non-object frames keep the prior opaque
event path. Error-shaped frames stay marked for Python because public error
conversion needs request context and controls retry/failover.

The Python native adapter carries this metadata through `NativeWebSocketMessage`,
including the already-decoded object payload needed by WebSocket request
matching, sequence tracking and tool-call policy.
The Responses stream loop trusts it for canonical and successfully normalized
frames, directly building the existing `data: ...` event block and terminal type
decision without another `json.loads` or alias pass. It still uses the existing
Python parser for error handoffs and for non-native WebSocket implementations.
This removes the duplicated native hot-path parser while preserving the policy
layer: archiving, request matching, downstream transformation, lifecycle close,
retry eligibility and account health remain Python-owned.

The capability is explicit (`websocket_responses_events_v1`) and the request
flag defaults false, so generic and Live helper calls keep their old wire shape.
The helper handshake remains fail-closed when an installed binary does not expose
the required capability, matching the existing native protocol policy.

## Current Python-to-Rust parity audit

The implementation was compared against the current main-branch Python behavior
after the HTTP interpretation migration. Alias names remain exactly:
`response.text.delta`, `response.audio.delta`, and
`response.audio_transcript.delta`. Python-owned error envelopes, non-object and
invalid frames, terminal event classification, and `enforce_openai_sdk_contract`
handling remain covered. No newer Python alias or lifecycle rule was found that
could be safely moved without also moving request state and retry policy.

## Verification (2026-09-08)

- Rust response/protocol/egress tests: 12 egress unit tests, 1 protocol
  handshake test, 4 WebSocket interpretation tests, shared compact/stream
  fixtures, and workspace Clippy/tests passed.
- Python WebSocket/native suites: 1,531 passed, 1 skipped only when the native
  binary environment variable was intentionally absent; the native routed wire
  probe passed separately with the release helper.
- Ruff, format, architecture, cancellation-safety and timing-seam checks passed.
- Direct/routed Responses WebSocket probe verified `response.completed` metadata;
  existing Live/opaque and send/close/liveness tests remained green.
- Strict OpenSpec validation passed before archive. Main spec validation is run
  after syncing this delta.

## Synthetic CPU evidence

The same release helper and loopback WebSocket sent 2,048 canonical delta
frames, with 3 warmups and 12 samples per mode. The raw mode performed the old
Python `json.loads` and type assertion; the interpreted mode trusted Rust
metadata. Median elapsed/helper CPU milliseconds were `524 / 455` raw and
`539 / 470` interpreted. This first implementation therefore has no speedup:
Rust owns the classification, but JSON parsing plus IPC serialization costs more
than the local Python parse in this synthetic setup. The result is retained to
prevent a false performance claim. The migration's value is one semantic owner
and reduced Python hot-path code; a later optimization can avoid reserializing
canonical frames or combine this with WebSocket output batching.

The reproduction script and result are preserved at
`/mnt/workspace/projects/codex-lb/rust-migration/2026-09-08-native-websocket-event-interpretation/`.

WebSocket output batching and broader lifecycle/retry migration remain separate
concerns. The next slice can measure trusted metadata CPU reduction before moving
additional policy out of Python.
