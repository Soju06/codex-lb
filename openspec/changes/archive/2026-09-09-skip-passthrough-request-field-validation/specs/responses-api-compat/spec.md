## ADDED Requirements

### Requirement: Passthrough Responses request fields are shape-checked, not deep-validated

The service MUST treat the `input`, `tools` and `text.format.schema` fields of `/backend-api/codex/responses` and `/v1/responses` requests, and the `messages` field of `/v1/responses` requests, as opaque JSON: it MUST NOT re-validate or coerce their nested values against a JSON value schema, and the nested values MUST reach the upstream payload byte-for-byte except where a documented normalization (tool type aliases, input sanitation, instruction hoisting) rewrites them. The service MUST still enforce the top-level shape locally: `input` MUST be a string or an array, `tools` MUST be an array, and `messages` MUST be an array when present. Because the upstream serializer cannot emit JSON nested deeper than roughly 250 container levels, a passthrough field whose objects/arrays nest deeper than 200 levels MUST be rejected at validation time rather than failing later while the request is being serialized. A shape or depth violation MUST be rejected with HTTP 400, `error.type = "invalid_request_error"`, and `error.param` naming the offending field. Non-finite numbers (for example `1e400`, which `json.loads` accepts but JSON cannot represent) serialize as `null` in the forwarded payload. The OpenAPI document MUST still be generated for every request model that declares these fields.

#### Scenario: Non-array tools are rejected with the tools param

- **WHEN** a client sends `/backend-api/codex/responses` or `/v1/responses` with `tools` set to `null`, a string, a number, or an object
- **THEN** the proxy returns HTTP 400 with `error.type = "invalid_request_error"` and `error.param = "tools"`
- **AND** no upstream connection is opened

#### Scenario: Non-array messages are rejected with the messages param

- **WHEN** a client sends `/v1/responses` with `messages` set to a string, a number, or an object
- **THEN** the proxy returns HTTP 400 with `error.type = "invalid_request_error"` and `error.param = "messages"`

#### Scenario: Non-string non-array input is still rejected

- **WHEN** a client sends `/backend-api/codex/responses` or `/v1/responses` with `input` set to a number, boolean, or object
- **THEN** the proxy returns HTTP 400 with `error.type = "invalid_request_error"` and `error.param = "input"`

#### Scenario: Deeply nested passthrough values are rejected with the field param

- **WHEN** a client sends `/backend-api/codex/responses` or `/v1/responses` (HTTP or the Responses WebSocket) with `input`, `tools`, `messages` or `text.format.schema` containing objects/arrays nested more than 200 levels deep, or `/backend-api/codex/responses/compact` with `input` nested more than 200 levels deep (`input` is the only passthrough field the compact model declares; `/v1/responses/compact` guards `input` and `messages`)
- **THEN** the proxy returns HTTP 400 (or a `status: 400` WebSocket error event) with `error.type = "invalid_request_error"` and `error.param` naming the field (`text.format.schema` for the schema)
- **AND** no upstream connection is opened

#### Scenario: Nested passthrough values are forwarded verbatim

- **GIVEN** a Responses request whose `tools[i].parameters` and `input` content contain nested arrays, objects, floats such as `1.0`, booleans and nulls
- **WHEN** the service builds the upstream payload
- **THEN** the serialized `tools` and untouched `input` entries are byte-identical to the client's JSON
- **AND** the `/v1/responses` conversion yields the same upstream payload bytes as before passthrough handling

#### Scenario: OpenAPI generation succeeds

- **WHEN** `GET /openapi.json` is requested
- **THEN** the document is returned with `V1ResponsesRequest`, `ResponsesCompactRequest` and the `/backend-api/codex/responses` request body schemas present
