## ADDED Requirements

### Requirement: Native SSE framing is an explicitly negotiated transport mode

The native protocol MUST advertise and the Python adapter MUST require
`http_sse_v1` before dispatch. HTTP requests MAY include SSE framing options
with positive idle timeout and event byte limit. Without these options, and
for HTTP statuses at least 400, the worker MUST preserve raw chunk output.
With these options and a successful HTTP response, Rust MUST emit complete
SSE text blocks and own byte framing and the deadline between body reads.
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
