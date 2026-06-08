## ADDED Requirements

### Requirement: Responses input images bypass the HTTP bridge

The service MUST bypass the HTTP responses bridge when a `/v1/responses`,
`/backend-api/codex/responses`, `/responses/compact`, or `/v1/responses/compact`
request contains any `input_image` part in top-level input items, nested
message content, or tool output content, and send the request over the raw HTTP
Responses stream path. This bypass MUST happen after rejecting unsupported
uploaded-image references and MUST be limited to the current request; subsequent
text-only requests MAY continue using the HTTP responses bridge.

The raw HTTP path is the source of truth for image validation and upstream image
error semantics. The bridge MUST NOT hold image requests waiting for
`response.created` when upstream rejects an invalid inline image payload.

#### Scenario: Nested input_image bypasses bridge

- **GIVEN** the HTTP responses bridge is enabled
- **WHEN** a Responses request contains a nested content part with `type = "input_image"`
- **THEN** the request is sent through the raw HTTP stream path
- **AND** the HTTP responses bridge is not used for that request

#### Scenario: Image bypass does not disable future text bridge use

- **GIVEN** the HTTP responses bridge is enabled
- **WHEN** an image-bearing request bypasses the bridge
- **THEN** the bypass applies only to that request
- **AND** a later text-only request can still use the HTTP responses bridge
