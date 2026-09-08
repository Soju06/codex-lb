## MODIFIED Requirements

### Requirement: Native SSE framing is an explicitly negotiated transport mode

The native protocol MUST advertise and the Python adapter MUST require
`http_sse_v1` before dispatch. HTTP requests MAY include SSE framing options
with positive idle timeout and event byte limit. Without these options, and
for HTTP statuses at least 400, the worker MUST preserve raw chunk output.
With these options and a successful HTTP response selected for framing, Rust
MUST emit complete SSE text blocks and own byte framing and the deadline between body reads.
Python MUST NOT reframe these blocks or replay a dispatched request.
IPC text fragments MUST be bounded to at most 16 KiB of UTF-8, with an explicit
continuation flag; the adapter MUST join them before exposing an event and
MUST reject a clean EOF that leaves an incomplete event.

#### Scenario: Old helper cannot silently ignore framing options

- **WHEN** an installed helper omits `http_sse_v1`
- **THEN** negotiation fails before HTTP dispatch
- **AND** the adapter does not fall back to another transport

#### Scenario: Partial bytes keep the upstream stream active

- **WHEN** body bytes arrive within each configured idle interval without completing an SSE block
- **THEN** the Rust body-read deadline resets on activity
- **AND** no premature event-wait timeout is introduced in Python

#### Scenario: HTTP error and ordinary body consumption

- **WHEN** SSE options are absent or the HTTP response status is at least 400
- **THEN** consumers receive the ordinary raw body chunk contract

#### Scenario: One framed request fails or is cancelled

- **WHEN** one framed stream exceeds its byte limit, times out, or is cancelled
- **THEN** only that attempt terminates and its stream registration is released
- **AND** unrelated requests sharing the helper remain usable
- **AND** failure diagnostics do not contain upstream body content or credentials


## ADDED Requirements

### Requirement: Compact native framing supports response content negotiation

The native protocol MUST advertise and the adapter MUST require
`http_compact_sse_v1` before dispatching content-type-aware SSE requests.
Such requests MUST frame successful responses when the Content-Type media type
is exactly `text/event-stream`, ignoring parameters and case, or when the header
is absent or empty. Other successful
responses and HTTP errors MUST retain raw body consumption. Ordinary SSE
requests without the content-type-aware option MUST retain their existing
framing behavior. A null native total timeout MUST mean no total deadline;
positive explicit total, connection, and SSE idle limits MUST be preserved.
The compact SSE idle limit MUST retain the existing Python policy: use the
effective compact timeout when set, otherwise use `stream_idle_timeout_seconds`.

#### Scenario: Compact success returns JSON

- **WHEN** a compact request receives a successful application/json response
- **THEN** the worker and adapter expose raw bytes for the existing JSON parser
- **AND** SSE event limits and decoding are not applied to that JSON body

#### Scenario: Compact success returns SSE or omits Content-Type

- **WHEN** a compact success has a text/event-stream or absent/empty Content-Type
- **THEN** Rust owns byte framing, original-byte limits, and body-read idle deadlines
- **AND** Python consumes framed text without rescanning bytes

#### Scenario: Non-SSE media type mentions event-stream

- **WHEN** compact succeeds with `text/event-stream+json` or a JSON Content-Type parameter containing `text/event-stream`
- **THEN** native and missing-helper Python transports use raw-body JSON parsing
- **AND** the SSE event byte limit does not apply to the JSON body

#### Scenario: Explicit compact timeout preserves its idle budget

- **WHEN** a compact request has an effective compact timeout longer than the ordinary stream idle timeout
- **THEN** native and missing-helper Python transports permit a body-read gap within that compact timeout
- **AND** the compact total deadline still bounds the complete request

#### Scenario: Optional total timeout

- **WHEN** compact has no total timeout
- **THEN** native transport does not substitute a default total timeout
- **AND** its configured connection and SSE idle limits remain active

#### Scenario: Incompatible helper

- **WHEN** an installed helper lacks http_compact_sse_v1
- **THEN** the adapter fails before dispatch rather than silently ignoring compact options
- **AND** it does not replay through Python
