## ADDED Requirements

### Requirement: Responses requests accept file-backed content items that reference an upload

The system SHALL accept file-backed content items that reference an upload by `file_id` in `/backend-api/codex/responses` and `/v1/responses` request payloads (both list-form and string-form `input`). The same MUST apply to `/responses/compact` request bodies.

Accepted request shapes are:
- `{"type": "input_file", "file_id": "file_abc"}`
- `{"type": "input_image", "file_id": "file_abc"}`
- `{"type": "input_image", "image_url": "sediment://file_abc"}`

The proxy MUST continue forwarding `input_file.file_id` items verbatim. For `input_image.file_id`, the proxy MUST rewrite the part into the canonical upstream form `{"type": "input_image", "image_url": "sediment://file_abc"}` before deriving any prompt-cache affinity key and before forwarding the request upstream. Non-sediment `input_image.image_url` values (for example HTTPS download URLs or `data:` URLs) MUST remain unchanged. The proxy MUST NOT raise `input_file.file_id is not supported` for accepted file-backed items.

#### Scenario: input_file with file_id is accepted in a /responses request

- **WHEN** a client posts a `/v1/responses` request whose `input` contains a `{"type": "input_file", "file_id": "file_abc"}` content item
- **THEN** the request validates and the upstream payload includes that content item unchanged

#### Scenario: input_file with file_id is accepted in a compact request

- **WHEN** a client posts a `/responses/compact` request whose `input` contains an `input_file` item with a `file_id`
- **THEN** the request validates and is forwarded to upstream verbatim

#### Scenario: input_image with file_id is normalized before forwarding

- **WHEN** a client posts a `/v1/responses` request whose `input` contains `{"type": "input_image", "file_id": "file_img"}`
- **THEN** the request validates
- **AND** the upstream payload contains `{"type": "input_image", "image_url": "sediment://file_img"}`
- **AND** the proxy does not trim or rewrite any unrelated conversation history

### Requirement: Responses requests with file-backed references route to the upload's account

A `/v1/responses`, `/backend-api/codex/responses`, or `/responses/compact` request that references an `{type: "input_file", file_id}`, `{type: "input_image", file_id}`, or `{type: "input_image", image_url: "sediment://file_id"}` item SHALL be routed to the upstream account that registered the file via `POST /backend-api/files`, when an in-memory pin for that `file_id` is still live. Stronger affinity signals MUST take precedence over the file_id pin: an explicit `prompt_cache_key`, a session header (`StickySessionKind.CODEX_SESSION`), a turn-state header, or a `previous_response_id` MUST keep their existing routing semantics.

When multiple `file_id`s are referenced and several are pinned, the most-recently-pinned one MUST be preferred (with a deterministic lexicographic tie-break on `file_id`).

#### Scenario: file_id pin drives routing for an input_file response

- **GIVEN** a `POST /backend-api/files` registered `file_xyz` through `account_a`
- **WHEN** a `/v1/responses` request references `{"type": "input_file", "file_id": "file_xyz"}` and has no stronger affinity
- **THEN** the proxy MUST route the request to `account_a`

#### Scenario: prompt_cache_key overrides the file_id pin

- **GIVEN** a pinned `file_xyz -> account_a`
- **WHEN** a `/v1/responses` request references `file_xyz` AND sets an explicit `prompt_cache_key`
- **THEN** the proxy MUST follow the prompt-cache affinity for routing and MUST NOT use the file_id pin

### Requirement: HTTP bridge clean closes without response events fail closed

When the `/v1/responses` HTTP bridge upstream websocket closes with `close_code=1000` before the proxy observes any `response.*` event for the pending request, the proxy SHALL classify that close as a permanent upstream rejection rather than a replayable transport race. The proxy MUST fail the request closed, surface an error to the client, and MUST NOT schedule `retry_precreated` / reconnect replay for that request.

#### Scenario: clean close before response.created is not replayed

- **GIVEN** a pending bridged `/v1/responses` request has not received any `response.*` event
- **WHEN** the upstream websocket closes cleanly with `close_code=1000`
- **THEN** the proxy returns a terminal error for that request
- **AND** the proxy does not enqueue a transparent replay on the HTTP bridge
