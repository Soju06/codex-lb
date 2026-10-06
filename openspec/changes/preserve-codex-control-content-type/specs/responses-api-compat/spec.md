## ADDED Requirements

### Requirement: Codex control requests preserve a single media-type header

For unary Codex control requests, including standalone search and realtime call
creation, the proxy MUST emit at most one case-insensitive `Content-Type` field.
When an inbound media type is present, the proxy MUST forward that value without
rewriting it to JSON and MUST preserve the opaque body bytes. Native requests
MUST retain the first media-type field's spelling and header position. The
contract MUST apply across direct and routed HTTP transports. With no inbound
media type, requests with a body MUST retain the existing JSON default, while
requests without a body MUST omit the synthesized media-type field.

#### Scenario: native standalone search preserves a single JSON media type

- **WHEN** Codex Desktop sends a JSON POST to any supported standalone search path
- **THEN** the upstream request contains exactly one `Content-Type` field with the inbound value
- **AND** the body bytes are unchanged

#### Scenario: non-JSON control body retains its media type

- **WHEN** a native control request carries `content-type: application/sdp`
- **THEN** the upstream request contains one lowercase media-type field in its original position
- **AND** its value remains `application/sdp` without a second JSON field

#### Scenario: header spelling does not create duplicates

- **WHEN** a native or SDK caller supplies a lowercase, PascalCase, or mixed-case media-type header
- **THEN** the control request emits exactly one case-insensitive media-type field

#### Scenario: bodyless control requests omit synthesized media type

- **WHEN** a control request has neither an inbound media type nor a body
- **THEN** the upstream request has no `Content-Type` field

#### Scenario: untyped control bodies retain the existing default

- **WHEN** a control request has a body and no inbound media type
- **THEN** the upstream request has one `Content-Type: application/json` field
