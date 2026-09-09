# Harden stale-anchor error shapes across streaming surfaces

## Why

The canonical stale-anchor signal is already defined for Codex-native Responses
streams, but the shared parsers still collapse an explicitly malformed
`error.param` into absence. That makes a malformed upstream frame eligible for
the same replay path as a verified `previous_response_id` error. The same
collapse also lets invalid metadata leak through one of the WebSocket, HTTP
Responses, or Chat Completions serializers.

Typeless error frames and nested `response.failed` frames are another boundary
case: they can be classified for settlement while still losing their error
shape or response identifier during public normalization. This change makes
those boundaries explicit without changing account selection or retry policy.

## What changes

- Preserve whether an upstream error supplied `param`, together with its raw
  JSON value, while parsing WebSocket, HTTP bridge, Responses, and Chat
  Completions errors.
- Keep recovery authorization fail-closed for present non-string, null, blank,
  or whitespace-only parameters. A malformed value may still be recognized as
  a stale-anchor-shaped error for public masking, but it never authorizes a
  full-history replay or account switch.
- Sanitize malformed public `param` values by omitting them; trim valid string
  parameters at response/event construction boundaries.
- Preserve the native event envelope on WebSocket and HTTP Responses
  serializers. The Chat Completions adapter intentionally translates terminal
  Responses events into the documented Chat Completions `{"error": ...}`
  envelope; it sanitizes that nested error detail without exposing native
  `response.failed` fields that are not part of the Chat contract.
- Classify typeless error payloads consistently and preserve a nested
  `response.failed` response id while masking its stale-anchor error details.
- Keep the already-defined Codex-native canonical
  `previous_response_not_found` signal and public `/v1` `stream_incomplete`
  masking unchanged for valid errors.

## Non-goals

- No new retry, account migration, durable-operation, retry-circuit,
  quarantine, or bridge ownership behavior.
- No changes to the existing OpenSpec canonical signal or to client code.
- No changes to ordinary invalid-request classification or recovery semantics.
  Their public envelopes remain otherwise unchanged; as required above,
  present malformed ``param`` metadata is omitted and valid string values are
  trimmed, while errors without ``param`` metadata are unchanged.

## Capabilities

### Modified capabilities

- `responses-api-compat`: error parsing and public serialization preserve
  explicit parameter presence, fail closed on malformed stale-anchor metadata,
  and retain terminal event correlation fields.
