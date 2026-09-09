## Context

`POST /v1/chat/completions` always asks the upstream Responses client for an SSE
iterator. The adapter converts that iterator either into Chat Completions SSE or
into a collected JSON response. Explicit terminal events are already mapped,
but natural iterator exhaustion is not tracked. This leaves the streaming
protocol unterminated and lets the collected path synthesize
`finish_reason=stop`.

The public `/v1/responses` path already uses `upstream_stream_truncated`,
`server_error`, and HTTP 502 for EOF before a terminal event. The Chat adapter
must match those machine-consumed semantics without depending on the proxy API
module.

## Goals / Non-Goals

**Goals:**

- Detect whether a terminal upstream Responses event was observed.
- Use the canonical `upstream_stream_truncated` code and `server_error` type.
- Finish streaming Chat errors with `data: [DONE]`.
- Keep explicit completion, incomplete, failure, and generator-cleanup behavior
  unchanged.

**Non-Goals:**

- Change public `/v1/responses` normalization.
- Change retry, account selection, keepalive, or reservation policy.
- Convert explicit `response.incomplete` into a transport error.
- Refactor unrelated Chat payload or tool-call mapping.

## Decisions

### Decision: detect truncation at the Chat adapter boundary

The adapter is the first layer that knows whether it observed a Chat-relevant
terminal Responses event. `stream_chat_chunks` will synthesize the error chunk
and `[DONE]` only when its mapped iterator exhausts without a terminal marker.
`collect_chat_completion` will return the equivalent error envelope before it
assembles a `ChatCompletion`.

This keeps the behavior correct for both the subscription route and any other
caller of the adapter without changing the public Responses pipeline.

### Decision: preserve canonical machine semantics

The synthesized error uses:

- code: `upstream_stream_truncated`
- type: `server_error`
- message: `Responses stream ended before a terminal event`

The existing route-level `_status_for_error` fallback maps that envelope to
HTTP 502. No Chat-specific status policy is added.

### Decision: leave explicit incomplete events successful

`response.incomplete` is a terminal event with a meaningful finish reason such
as `length` or `content_filter`. It remains a Chat completion. Only EOF without
any terminal event is classified as transport truncation.

## Risks / Trade-offs

- A caller that previously relied on partial EOF content will now receive a
  retriable error. That is intentional because presenting partial output as
  complete is contract-breaking and suppresses retries.
- Streaming may already have emitted partial content before the error. The
  terminal error chunk and `[DONE]` make that state explicit without retracting
  bytes already delivered.

## Test Strategy

- Unit-test streaming delta-then-EOF for error + `[DONE]`.
- Unit-test collected delta-then-EOF for the canonical error envelope.
- Route-test non-streaming HTTP status and error code.
- Manually drive streaming and non-streaming ASGI requests with an inert
  delta-then-EOF upstream.
- Keep existing explicit completion, incomplete, error, usage, tool-call, and
  generator-close tests green.
