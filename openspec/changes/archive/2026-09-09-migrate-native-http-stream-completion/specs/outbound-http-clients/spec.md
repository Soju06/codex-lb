## ADDED Requirements

### Requirement: Rust owns recognized native HTTP Responses completion

The native adapter MUST require `http_responses_completion_v1` before dispatch.
For interpreted HTTP Responses streams, Rust MUST stop reading the upstream body
after classifying `response.completed`, `response.failed`, or
`response.incomplete`, deliver the entire terminal event, and release the body
without waiting for EOF. It MUST ignore subsequent bytes of that exchange.
Only the final fragment of a recognized terminal MAY carry `stream_complete=true`.
An absent completion marker MUST mean false. Python MUST validate this marker and retire that request without sending cancel
once its complete terminal block has been assembled. Malformed or truncated
fragments MUST fail without replay. Cancellation before completion and bounded
queue overflow MUST still release that request without invalidating its peers.

#### Scenario: Upstream remains open after a terminal

- **WHEN** a direct or routed native HTTP stream sends a recognized terminal and leaves its body open
- **THEN** the full terminal reaches the consumer and Rust releases the upstream body
- **AND** Python sends no cancellation command for normal completion

#### Scenario: Fragmented terminal followed by invalid bytes

- **WHEN** a recognized terminal spans multiple IPC fragments and is followed by an oversized event
- **THEN** all terminal fragments are delivered before completion
- **AND** the later event causes no size failure or additional output

#### Scenario: Cancellation or malformed completion

- **WHEN** a consumer cancels before completion or receives an invalid completion marker
- **THEN** its request is cleaned up without replay
- **AND** another request on the same helper remains usable

#### Scenario: Other lifecycle owners remain active

- **WHEN** a stream uses raw HTTP, uninterpreted SSE, compact collection, a Python fallback, or a persistent WebSocket
- **THEN** that transport retains its existing termination protocol
