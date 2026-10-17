## Why

Non-streaming `POST /v1/chat/completions` takes the first SSE item off the
upstream generator, then collects through a prepend wrapper. When that first
item is `response.failed`, collect returns without closing the original
generator. The stream `finally` (API-key reservation release and lease
cleanup) does not run, and the route maps every collected error to HTTP 502
except a small unavailable-selection set. The same failure on
`POST /v1/responses` drains the generator and uses `_status_for_error`
(429 for `rate_limit_exceeded`).

## What Changes

- Close the upstream `stream_responses` generator after non-stream chat
  collect, including early `response.failed` / `error` returns.
- Map collected Chat Completions error envelopes with the same HTTP status
  helper as non-stream `/v1/responses`.

## Capabilities

### Modified Capabilities

- `chat-completions-compat`: non-stream chat settles the upstream generator
  and returns the Responses-aligned error status.

## Impact

Streaming Chat Completions and `/v1/responses` collect are unchanged. Clients
that already treated every non-stream chat error as 502 will now see 429/401/
400 when the envelope code already implied that.
