## ADDED Requirements

### Requirement: Native Responses WebSocket frames are interpreted in Rust

When a native WebSocket request opts into Responses interpretation, the helper
MUST classify JSON object frames and embed their original JSON payload in IPC.
The Python adapter MUST reuse the payload decoded by the IPC reader for native
WebSocket request matching and event processing. The helper MUST preserve
original frame text, JSON numbers, duplicate-key precedence and the request
identifier. Invalid JSON, non-object frames, frames larger than 1 MiB (1,048,576
UTF-8 bytes), and objects that cannot be classified losslessly MUST retain opaque
delivery. Live WebSockets MUST remain opaque.

A string `type` MUST take precedence, including an empty string; otherwise an
object-valued `error` MUST classify as `error`. The native WebSocket boundary MUST
preserve aliases unchanged, matching the existing WebSocket relay. Error
conversion, HTTP-specific normalization, request matching, lifecycle and retry
policy MUST remain with their existing Python consumers. Rust MUST NOT close a
socket merely because it classified a terminal event.

#### Scenario: Canonical delta retains request ownership

- **WHEN** a native Responses delta includes a response id and sequence number
- **THEN** Python uses the decoded payload and Rust event type without reparsing
- **AND** request matching, sequence tracking and downstream text remain unchanged

#### Scenario: Alias preserves WebSocket relay behavior

- **WHEN** a WebSocket frame uses a legacy event alias
- **THEN** Rust preserves its original type and text
- **AND** any HTTP-specific normalization remains at the HTTP conversion boundary

#### Scenario: Numeric values remain exact

- **WHEN** a frame carries an integer larger than 64 bits or a float
- **THEN** its original numeric tokens reach the Python IPC decoder without Rust numeric conversion
- **AND** downstream text is identical to the upstream text

#### Scenario: Error envelope retains Python ownership

- **WHEN** a typeless frame contains an object error or has an explicit string type
- **THEN** Rust applies string-type precedence before classifying a typeless error
- **AND** Python retains public error conversion and retry policy with the original payload

#### Scenario: HTTP bridge preserves its existing framing semantics

- **WHEN** a native frame is a single-line object beginning with an opening brace
- **THEN** the HTTP bridge reuses its decoded payload and event type
- **AND** other text shapes retain the existing SSE-field parsing behavior

#### Scenario: Live WebSocket remains opaque

- **WHEN** a native Live WebSocket receives text or binary frames
- **THEN** the helper emits the existing opaque WebSocket events
