# responses-api-compat delta

## MODIFIED Requirements

### Requirement: Responses input images bypass the HTTP bridge

The service MUST bypass the HTTP responses bridge when a `/v1/responses`,
`/backend-api/codex/responses`, `/responses/compact`, or `/v1/responses/compact`
request contains any `input_image` part in top-level input items, nested
message content, or tool output content, and send the request over the raw HTTP
Responses stream path, **unless every `input_image` part is a validated inline
image**. This bypass MUST happen after rejecting unsupported uploaded-image
references and MUST be limited to the current request; subsequent text-only
requests MAY continue using the HTTP responses bridge.

An inline `input_image` part is validated when ALL of the following hold:

- the part carries an inline `data:image/<type>;base64,...` URL;
- the base64 payload decodes;
- the decoded bytes match the declared media type and carry parseable
  dimensions (PNG IHDR, GIF header, JPEG SOF, or WebP VP8X/VP8/VP8L);
- each dimension is at least 64 pixels and the total pixel count is at most
  100,000,000;
- the serialized request fits the WebSocket transport payload budget.

When every `input_image` part in the request is validated this way and the
payload fits the WebSocket budget, the HTTP responses bridge MAY be used for
the request instead of the raw HTTP path. When any `input_image` part fails
validation (or the payload exceeds the WebSocket budget), the service MUST
send the request over the raw HTTP stream path with the upstream stream
transport forced to HTTP.

The raw HTTP path is the source of truth for image validation and upstream image
error semantics. The bridge MUST NOT hold image requests waiting for
`response.created` when upstream rejects an invalid inline image payload.

#### Scenario: Nested input_image bypasses bridge

- **GIVEN** the HTTP responses bridge is enabled
- **WHEN** a Responses request contains a nested content part with `type = "input_image"`
- **AND** the image is not a validated inline image (for example, an external URL or undecodable base64)
- **THEN** the request is sent through the raw HTTP stream path
- **AND** the HTTP responses bridge is not used for that request

#### Scenario: Validated inline image uses the bridge

- **GIVEN** the HTTP responses bridge is enabled
- **WHEN** a Responses request contains an `input_image` part with a valid inline
  `data:image/png;base64,...` URL whose decoded bytes parse as a PNG with
  dimensions within the allowed range
- **AND** the serialized request fits the WebSocket transport payload budget
- **THEN** the request MAY be sent through the HTTP responses bridge
- **AND** the request is not forced onto the raw HTTP stream path

#### Scenario: Degenerate inline image bypasses the bridge

- **GIVEN** the HTTP responses bridge is enabled
- **WHEN** a Responses request contains an `input_image` part with a valid base64
  PNG that is 1x1 pixel
- **THEN** the request is sent through the raw HTTP stream path with the
  upstream stream transport forced to HTTP

#### Scenario: Image bypass does not disable future text bridge use

- **GIVEN** the HTTP responses bridge is enabled
- **WHEN** an image-bearing request bypasses the bridge
- **THEN** the bypass applies only to that request
- **AND** a later text-only request can still use the HTTP responses bridge
