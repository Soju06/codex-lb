## ADDED Requirements

### Requirement: Passthrough Responses request fields are shape-checked, not deep-validated

The service MUST treat the `input`, `tools` and `text.format.schema` fields of `/backend-api/codex/responses` and `/v1/responses` requests, and the `messages` field of `/v1/responses` requests, as opaque JSON: it MUST NOT re-validate or coerce their nested values against a JSON value schema, and the nested values MUST reach the upstream payload byte-for-byte except where a documented normalization (tool type aliases, input sanitation, instruction hoisting) rewrites them. The service MUST still enforce the top-level shape locally: `input` MUST be a string or an array, `tools` MUST be an array, and `messages` MUST be an array when present. A shape violation MUST be rejected with HTTP 400, `error.type = "invalid_request_error"`, and `error.param` naming the offending field. The OpenAPI document MUST still be generated for every request model that declares these fields.

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

#### Scenario: Nested passthrough values are forwarded verbatim

- **GIVEN** a Responses request whose `tools[i].parameters` and `input` content contain nested arrays, objects, floats such as `1.0`, booleans and nulls
- **WHEN** the service builds the upstream payload
- **THEN** the serialized `tools` and untouched `input` entries are byte-identical to the client's JSON
- **AND** the `/v1/responses` conversion yields the same upstream payload bytes as before passthrough handling

#### Scenario: OpenAPI generation succeeds

- **WHEN** `GET /openapi.json` is requested
- **THEN** the document is returned with `V1ResponsesRequest`, `ResponsesCompactRequest` and the `/backend-api/codex/responses` request body schemas present
