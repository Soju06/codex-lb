## Why

When the HTTP responses bridge receives an upstream terminal `response.failed`
or `error` frame as the first stream item, the route startup probe currently
converts that event into a non-streaming HTTP JSON error. Streaming clients then
do not receive the upstream terminal SSE event for actionable failures such as
`context_length_exceeded` or `overloaded_error`.

## What Changes

- Preserve upstream terminal error events on streaming `/v1/responses` and
  `/backend-api/codex/responses` bridge streams.
- Keep true startup exceptions, such as local capacity/admission failures, as
  pre-stream HTTP error responses.

## Impact

- Streaming bridge clients receive OpenAI-compatible SSE terminal events for
  upstream failures.
- Non-streaming collection and startup exception behavior are unchanged.
