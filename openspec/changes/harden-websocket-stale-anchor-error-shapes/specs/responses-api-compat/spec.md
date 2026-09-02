# responses-api-compat Delta

## ADDED Requirements

### Requirement: Stale-anchor error parameters preserve presence and fail closed

When the proxy parses an upstream Responses or Chat Completions error, it MUST
distinguish an absent `param` from a present malformed value. A present
non-string, null, blank, or whitespace-only `param` MUST NOT authorize
previous-response recovery, full-history replay, account migration, or any
other proof-gated retry. A valid string parameter MAY be normalized by trimming
surrounding whitespace before public serialization.

#### Scenario: malformed parameter cannot authorize recovery

- **GIVEN** an anchored request receives a canonical stale-anchor code or
  previous-response-not-found message with `param = null`, a non-string value,
  or blank whitespace
- **WHEN** the proxy evaluates replay eligibility
- **THEN** the request fails closed and remains in the existing terminal path
- **AND** no unanchored replay or account switch is authorized

#### Scenario: absent parameter keeps the narrow parameterless classifier

- **GIVEN** an anchored request receives `code = invalid_request_error`, no
  `param`, and the exact normalized `Invalid previous_response_id.` message
- **WHEN** the proxy evaluates continuity recovery
- **THEN** the existing parameterless previous-response classifier may match
- **AND** unrelated invalid-request messages remain unmatched

### Requirement: Public error serializers omit malformed parameter metadata

When a public WebSocket or HTTP Responses serializer emits an error containing
a present malformed `param`, it MUST omit that field. It MUST preserve the
native event envelope and MUST NOT expose a raw stale `previous_response_id`.
A valid string parameter MUST remain available in trimmed form. This
sanitization applies regardless of the error code or message; only an error
that has no `param` metadata is left unchanged. When stale-anchor masking
applies, its generic terminal envelope takes precedence and may remove even a
valid `previous_response_id` parameter.

The Chat Completions adapter MUST apply the same parameter sanitization to the
nested error detail, but it MUST retain the documented Chat Completions error
envelope rather than forwarding the native Responses event type or outer
`response` object.

#### Scenario: native malformed error is sanitized without changing its shape

- **GIVEN** a native terminal `error` or `response.failed` frame contains a
  present malformed `param`
- **WHEN** the native stream is serialized for the client
- **THEN** the error retains its event type and other fields
- **AND** the malformed `param` is omitted

#### Scenario: Chat Completions errors keep their adapter envelope

- **GIVEN** a Chat Completions stream receives a native terminal error with a
  present malformed `param`
- **WHEN** the Chat adapter serializes the error
- **THEN** it emits the documented `{"error": ...}` Chat Completions shape
- **AND** the nested malformed `param` is omitted
- **AND** native Responses-only fields are not forwarded

#### Scenario: public stale-anchor error remains generic

- **GIVEN** a public `/v1/responses` stream receives a stale-anchor error,
  including a typeless or nested `response.failed` shape
- **WHEN** the API normalizes the stream
- **THEN** it emits the existing `stream_incomplete` envelope
- **AND** it removes the stale anchor metadata while preserving the nested
  response id when one was supplied

### Requirement: Typeless terminal errors retain settlement and correlation data

The streaming normalizers MUST classify a payload with a dictionary `error`
and no string `type` as an `error` event for terminal settlement. A nested
`response.failed` error MUST retain its outer response identifier when its
error details are masked or sanitized. A valid native error frame that needs no
sanitization MUST remain byte-identical.

#### Scenario: typeless error flushes pending terminal-adjacent state

- **GIVEN** a stream has buffered reasoning-summary data followed by a typeless
  error payload
- **WHEN** the normalizer processes the error
- **THEN** it flushes the buffered data before forwarding the terminal error

#### Scenario: nested terminal masking preserves response id

- **GIVEN** a `response.failed` payload has an outer `response.id` and a stale
  previous-response error
- **WHEN** the public normalizer masks the stale error
- **THEN** the terminal event keeps the same `response.id`
- **AND** the error details are the generic `stream_incomplete` shape
