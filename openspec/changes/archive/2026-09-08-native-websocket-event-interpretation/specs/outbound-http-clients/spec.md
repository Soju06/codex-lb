## ADDED Requirements

### Requirement: Native Responses WebSocket frames are interpreted in Rust

When a native WebSocket request opts into Responses interpretation, the helper
MUST parse each text frame as a JSON object, ignore invalid JSON and non-object
frames as the existing Python stream does, normalize the supported Responses
aliases, and attach the event type plus whether Python normalization is still
required. It MUST preserve the frame text and request identifier, and MUST NOT
apply this behavior to Live WebSocket requests.

A frame carrying an error envelope MUST remain marked for Python normalization so
request context and the existing public error contract stay Python-owned. Rust
MUST classify terminal event types without closing the socket; Python retains
terminal lifecycle and retry decisions.

#### Scenario: Canonical delta uses trusted metadata

- **WHEN** a Responses WebSocket text frame is a valid canonical delta object
- **THEN** the native event carries its text and event type with Python normalization false
- **AND** Python forwards the equivalent framed event without reparsing the JSON

#### Scenario: Alias is normalized once

- **WHEN** a frame uses a supported legacy event alias
- **THEN** Rust emits the canonical event text and type
- **AND** Python does not run the legacy alias normalizer again

#### Scenario: Error envelope retains Python ownership

- **WHEN** a frame is an error event or contains an error envelope
- **THEN** Rust marks it for Python normalization
- **AND** Python preserves request context, public error conversion and retry policy

#### Scenario: Live WebSocket remains opaque

- **WHEN** a native Live WebSocket receives text or binary frames
- **THEN** the helper emits the existing opaque WebSocket events without Responses interpretation
