## ADDED Requirements

### Requirement: Responses requests accept input_file content items with a file_id

The system SHALL accept `input_file` content items that reference an upload by `file_id` in `/backend-api/codex/responses` and `/v1/responses` request payloads (both list-form and string-form `input`). These items MUST be forwarded to upstream verbatim. The same MUST apply to `/responses/compact` request bodies. The proxy MUST NOT raise `input_file.file_id is not supported` for these items.

#### Scenario: input_file with file_id is accepted in a /responses request

- **WHEN** a client posts a `/v1/responses` request whose `input` contains a `{"type": "input_file", "file_id": "file_abc"}` content item
- **THEN** the request validates and the upstream payload includes that content item unchanged

#### Scenario: input_file with file_id is accepted in a compact request

- **WHEN** a client posts a `/responses/compact` request whose `input` contains an `input_file` item with a `file_id`
- **THEN** the request validates and is forwarded to upstream verbatim

### Requirement: Responses requests with input_file.file_id route to the upload's account

A `/v1/responses`, `/backend-api/codex/responses`, or `/responses/compact` request that references an `{type: "input_file", file_id}` content item SHALL be routed to the upstream account that registered the file via `POST /backend-api/files`, when an in-memory pin for that `file_id` is still live. Stronger affinity signals MUST take precedence over the file_id pin: an explicit `prompt_cache_key`, a session header (`StickySessionKind.CODEX_SESSION`), a turn-state header, or a `previous_response_id` MUST keep their existing routing semantics.

When multiple `file_id`s are referenced and several are pinned, the most-recently-pinned one MUST be preferred (with a deterministic lexicographic tie-break on `file_id`).

#### Scenario: file_id pin drives routing for an input_file response

- **GIVEN** a `POST /backend-api/files` registered `file_xyz` through `account_a`
- **WHEN** a `/v1/responses` request references `{"type": "input_file", "file_id": "file_xyz"}` and has no stronger affinity
- **THEN** the proxy MUST route the request to `account_a`

#### Scenario: prompt_cache_key overrides the file_id pin

- **GIVEN** a pinned `file_xyz -> account_a`
- **WHEN** a `/v1/responses` request references `file_xyz` AND sets an explicit `prompt_cache_key`
- **THEN** the proxy MUST follow the prompt-cache affinity for routing and MUST NOT use the file_id pin

### Requirement: Responses requests inline-rewrite uploaded input_image references

The system SHALL accept the following attached-file shapes in `/v1/responses`, `/backend-api/codex/responses`, and `/responses/compact` request payloads:

- `{"type":"input_file","file_id":"file_*"}` forwarded verbatim
- `{"type":"input_image","file_id":"file_*"}` rewritten to an inline `data:` URL
- `{"type":"input_image","image_url":"sediment://file_*"}` rewritten to an inline `data:` URL

For `input_image` upload references, the proxy MUST resolve the file pin to the upload's owning account, fetch bytes from the pinned finalize `download_url`, run a codex-faithful image processing pipeline, and replace only the referenced `input_image` part with an inline `data:{mime};base64,{b64}` URL before forwarding. The rewrite pipeline MUST whitelist `PNG`, `JPEG`, `GIF`, and `WebP`; preserve PNG/JPEG/WebP bytes verbatim when the image already fits within 2048x2048; re-encode GIF as PNG; resize oversized images to fit within 2048x2048; keep resized PNG/JPEG/WebP in their source format; use JPEG quality 85 for resized JPEG; and use lossless encoding for resized WebP. The proxy MUST cap each fetched attachment at 16 MiB and reject larger items with HTTP 400 `file_too_large`.

The proxy MUST NOT trim, slim, or rewrite any conversation content other than the `input_image` parts that reference an upload.

#### Scenario: input_image file_id is rewritten inline before forwarding

- **GIVEN** a finalized upload pin for `file_img`
- **WHEN** a `/v1/responses` request contains `{"type":"input_image","file_id":"file_img"}`
- **THEN** the proxy fetches the upload bytes from the pinned `download_url`
- **AND** forwards the request with that part rewritten to an inline `data:image/...;base64,...` URL

#### Scenario: sediment upload URL is rewritten inline before forwarding

- **GIVEN** a finalized upload pin for `file_img`
- **WHEN** a `/responses/compact` request contains `{"type":"input_image","image_url":"sediment://file_img"}`
- **THEN** the proxy rewrites only that `input_image` part to an inline `data:` URL before forwarding

#### Scenario: missing image pin fails the whole request

- **WHEN** a `/v1/responses` request references `input_image.file_id = "file_missing"` and no live finalized pin exists
- **THEN** the proxy returns HTTP 400 with `error.code = "file_not_found"`
- **AND** does not partially forward other images from the same request

#### Scenario: large inline-rewritten payload routes via HTTP transport on auto

- **GIVEN** `upstream_stream_transport` is `"auto"` and the rewritten payload size exceeds the WebSocket frame budget
- **WHEN** the proxy resolves the upstream transport
- **THEN** the request MUST be sent over HTTP `POST` instead of WebSocket
- **AND** explicit `upstream_stream_transport = "websocket"` overrides MUST still take precedence

#### Scenario: large inline-rewritten payload bypasses the HTTP responses bridge

- **GIVEN** the HTTP responses bridge is enabled and the rewritten payload exceeds the WebSocket frame budget
- **WHEN** the proxy receives a `/v1/responses`, `/backend-api/codex/responses`, or `/responses/compact` request
- **THEN** the bridge MUST be bypassed for that request and the request MUST be sent over raw HTTP
- **AND** subsequent smaller requests MUST continue to use the bridge normally

### Requirement: Clean upstream close before any response event fails fast

When the HTTP responses bridge observes an upstream websocket close with `close_code = 1000` before any `response.*` event has been surfaced for the pending request, the proxy MUST classify the close as rejected input, surface HTTP 502 `upstream_rejected_input`, and MUST NOT trigger `retry_precreated` or `retry_fresh_upstream`.

#### Scenario: clean close before response.created is not retried

- **WHEN** upstream closes the HTTP responses bridge with `close_code = 1000` before any `response.*` event for the pending request
- **THEN** the proxy returns HTTP 502 with `error.code = "upstream_rejected_input"`
- **AND** does not transparently replay the pre-created request
