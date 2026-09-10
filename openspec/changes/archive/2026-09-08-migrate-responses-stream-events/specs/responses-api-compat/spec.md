## ADDED Requirements

### Requirement: Native HTTP interprets Responses stream events compatibly

Native direct and routed HTTP Responses streams MUST preserve the existing event
classification and legacy text/audio/audio-transcript alias normalization.
Unmodified events MUST retain their original text. SSE field parsing MUST
recognize only CR, LF, and CRLF boundaries and join data fields with LF.
Canonical event-line classification, malformed JSON, native passthrough mode,
unknown fields, and terminal behavior MUST match the Python path.

The synchronous Rust Responses library MUST own eligible alias normalization and
event classification. Python MUST retain request-context-dependent error mapping.
When alias serialization contains floating numbers, integers outside the Rust
JSON integer domain, or escaped surrogate strings, the interpreter MUST signal
Python normalization of the original event rather than lose data or reinterpret
its representation. This handoff MUST NOT replay the request.

#### Scenario: Legacy alias on framing and payload

- **WHEN** an event uses a supported legacy alias in its event line or payload
- **THEN** both surfaces are normalized according to the existing Python rules
- **AND** unrelated data and fields retain their values

#### Scenario: Unsupported alias JSON representation

- **WHEN** rewriting an alias requires a JSON representation outside the native serializer's supported domain
- **THEN** Python receives the original event and an explicit normalization marker
- **AND** the public result matches the ordinary Python transport

#### Scenario: Native error passthrough

- **WHEN** native passthrough receives an error event
- **THEN** it remains an error event and ends the stream under the existing policy
- **AND** SDK mode retains the existing request-context-dependent error conversion
