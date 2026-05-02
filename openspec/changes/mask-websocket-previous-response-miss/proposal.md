# Mask WebSocket previous-response misses

## Why

Direct Responses WebSocket follow-ups can receive an anonymous upstream `previous_response_not_found` after the upstream handshake has already succeeded. The proxy must treat that as continuity loss, not replay the same stale anchor in a loop or expose the raw upstream 400 to Codex clients.

## What Changes

- Short WebSocket continuations that depend on `previous_response_id` fail closed as retryable `stream_incomplete` without replaying the same stale anchor.

## Impact

Clients stop seeing raw `previous_response_not_found` frames for direct WebSocket continuity loss. Continuations receive the existing retryable `stream_incomplete` contract instead of retrying a stale anchor inside the proxy.
