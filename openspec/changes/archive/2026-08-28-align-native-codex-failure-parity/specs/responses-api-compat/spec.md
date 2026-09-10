## ADDED Requirements

### Requirement: Native Codex HTTP attempts preserve client transport choice

For a downstream HTTP/SSE Responses request identified as a native Codex
request by the existing first-party `User-Agent` or `originator` rules, the
proxy MUST retain upstream HTTP when transport is otherwise controlled by the
HTTP downstream policy. This native pin MUST take precedence over sticky
continuation signals and the `smart` or `always_websocket` policy, but it MUST
NOT override an explicit operator `upstream_stream_transport="websocket"` or
an existing higher-precedence mandatory transport rail. Native downstream
WebSocket requests MUST remain on their dedicated WebSocket path.

#### Scenario: Codex HTTP fallback is not promoted again

- **GIVEN** a native Codex client retries a WebSocket turn as an HTTP request
- **AND** the HTTP request carries a prompt cache key or Codex session header
- **WHEN** the configured transport is automatic and the HTTP policy is smart
- **THEN** codex-lb sends the attempt upstream over HTTP

#### Scenario: Explicit WebSocket remains authoritative

- **GIVEN** a native Codex HTTP request
- **WHEN** the operator explicitly configures upstream WebSocket transport
- **THEN** the explicit WebSocket selection remains authoritative

### Requirement: Native Codex preserves upstream failure lifecycle

For a native Codex HTTP/SSE Responses request, an upstream transport timeout or
stream EOF without a terminal Responses event MUST terminate the downstream
stream without synthesizing `response.failed`, `error`, or `[DONE]`. The proxy
MUST still execute reservation, request-log, account-health, and owned-resource
cleanup before propagating the termination. Non-native and OpenAI-compatible
clients MUST retain the existing stable terminal-error shaping.

#### Scenario: Native Codex sees a truncated SSE lifecycle

- **GIVEN** a native Codex HTTP request has received a non-terminal SSE event
- **WHEN** upstream closes without a terminal event
- **THEN** downstream closes without a synthetic terminal event or `[DONE]`
- **AND** proxy cleanup and failure accounting still complete

#### Scenario: Non-native client keeps the terminal umbrella

- **GIVEN** an OpenAI SDK or other non-native client receives the same upstream
  truncation
- **WHEN** codex-lb normalizes the stream
- **THEN** the client receives the existing terminal `response.failed` shape

### Requirement: Propagated upstream rate limits preserve Retry-After

When a Responses upstream HTTP rejection carries a valid `Retry-After` header
and that rejection is propagated as a downstream HTTP response, codex-lb MUST
copy the field value unchanged. The proxy MUST accept only a bounded value with
no CR or LF and MUST NOT expose other upstream response headers through this
rule. A missing or invalid value MUST remain absent.

#### Scenario: Upstream 429 retry hint survives startup propagation

- **WHEN** upstream rejects a Responses request with HTTP 429 and
  `Retry-After: 1`
- **THEN** the propagated downstream 429 includes `Retry-After: 1`

#### Scenario: Unsafe retry hint is omitted

- **WHEN** an upstream retry hint contains a line break or exceeds the bounded
  field length
- **THEN** codex-lb does not copy that value downstream
