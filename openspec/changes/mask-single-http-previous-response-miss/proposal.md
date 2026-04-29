# Mask single HTTP bridge previous-response misses

## Why

HTTP bridge follow-ups can receive an anonymous upstream `previous_response_not_found` error for the only pending request on a bridge session. The multi-request and response-id cases already rewrite this continuity loss to a retryable bridge error, but the single anonymous case can leak the raw upstream 400 to Codex clients.

That raw error makes clients treat the previous response as permanently invalid instead of retrying the bridge, causing intermittent session failures when continuity state was only lost inside the proxy/upstream bridge.

## What Changes

- Rewrite single-request anonymous HTTP bridge `previous_response_not_found` events the same way as the existing multi-request and response-id cases.
- Keep the response HTTP status retryable (`502`) and hide `previous_response_not_found` from downstream clients.
- Add regression coverage for a single pending HTTP bridge follow-up with no upstream response id.

## Impact

Codex CLI and OpenAI-compatible clients see a retryable `stream_incomplete` bridge failure instead of a raw `previous_response_not_found` 400 when a bridged upstream session loses continuity for one pending follow-up.
