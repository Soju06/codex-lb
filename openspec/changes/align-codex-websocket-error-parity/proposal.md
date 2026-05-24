# Align Codex WebSocket error parity

## Why

Official Codex's ChatGPT-backed Responses WebSocket client treats `type: "error"` frames with `status` or `status_code` as wrapped upstream HTTP errors and keeps the original frame body for error parsing. The proxy already masks nested and top-level `previous_response_not_found` frames, but its helper path still treats the event discriminator (`type: "error"`) as the error type when top-level fields are present and only reads `status`, not the official client's `status_code` alias.

That leaves parity gaps for ChatGPT backend error frames that arrive without a nested `error` object, especially when downstream classification depends on error type/status metadata.

## What changes

- Normalize top-level ChatGPT WebSocket error fields into an error-detail shape before classification.
- Accept `status_code` as an alias for `status` on wrapped WebSocket error frames.
- Preserve existing fail-closed masking for `previous_response_not_found` so raw anchors do not leak to Codex clients.
- Add regression coverage pinned to the official Codex client's observed wrapped-error contract.

## Official Codex reference

Analysis source: `openai/codex` at `7d47056ea42636271ac020b86347fbbef49490aa`.

Relevant files:

- `codex-rs/core/src/client.rs`
  - `ModelClientSession` is per turn.
  - It reuses one Responses WebSocket connection during the turn.
  - Incremental `response.create` payloads include `previous_response_id` only after a completed prior response id is available and the new input is an incremental extension of the previous request + output baseline.
  - Reconnect resets `last_request` and `last_response_rx`, so previous-response anchors are connection/session scoped optimizations, not durable resume tokens.
- `codex-rs/codex-api/src/endpoint/responses_websocket.rs`
  - Handshake uses `provider.websocket_url_for_path("responses")`, `OpenAI-Beta: responses_websockets=2026-02-06`, `x-codex-turn-state`, session headers, and optional compression/custom CA support.
  - Request frames are JSON text frames (`response.create` / `response.processed`).
  - `parse_wrapped_websocket_error_event` accepts only `type: "error"` frames.
  - `WrappedWebsocketErrorEvent.status` has `#[serde(alias = "status_code")]`.
  - Non-2xx wrapped errors map to `TransportError::Http` with the original frame body preserved.
  - `websocket_connection_limit_reached` maps to retryable so a new WebSocket can be opened.

## Impact

This is a narrow compatibility fix for the Responses WebSocket proxy path. It does not change account selection, durable bridge ownership, or public OpenAI-compatible SSE contracts except for more accurate wrapped-error normalization.