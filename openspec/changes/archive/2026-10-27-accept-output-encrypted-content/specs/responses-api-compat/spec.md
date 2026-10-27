## ADDED Requirements

### Requirement: Responses accept encrypted output inclusion

The service MUST accept `output.encrypted_content` in the `include` array of native and OpenAI-compatible Responses requests over HTTP and WebSocket. It MUST forward that selector unchanged, including when it accompanies `reasoning.encrypted_content`. Existing supported selectors and omitted or empty `include` arrays MUST retain their behavior. Unknown selectors, null arrays and non-string entries MUST remain invalid.

#### Scenario: Encrypted output is requested through either Responses API
- **WHEN** a client requests `include: ["reasoning.encrypted_content", "output.encrypted_content"]` through `/backend-api/codex/responses` or `/v1/responses`, using HTTP or a WebSocket `response.create`
- **THEN** include validation succeeds
- **AND** the upstream request retains both selectors

#### Scenario: Existing selectors and defaults remain accepted
- **WHEN** a request uses an existing supported selector or omits `include` or provides an empty array
- **THEN** its include validation and forwarding behavior remain unchanged

#### Scenario: Invalid include values remain rejected
- **WHEN** a request contains an unknown include selector, a null include array, or a non-string array entry
- **THEN** the request is rejected before upstream dispatch
- **AND** the HTTP response is a 400 `invalid_request_error` whose parameter identifies `include` or its invalid entry

### Requirement: Responses preserve opaque encrypted output for subsequent turns

For supported output item types, the service MUST preserve `encrypted_content` string values, item IDs, message `phase`, tool `call_id` and `encrypted_function_args` when present through HTTP responses and WebSocket item events. Subsequent requests containing those returned items MUST retain those fields during forwarding. The existing public terminal-output backfill and native item-event semantics MUST remain unchanged. An input payload retaining ciphertext on a message or tool call MUST NOT be classified as account-neutral fresh replay, including after item-ID projection.

#### Scenario: Encrypted items survive transport and replay
- **WHEN** upstream returns supported encrypted output items and the client sends them back in the next turn
- **THEN** the downstream output and next upstream input retain the opaque fields and corresponding item identities

#### Scenario: Output arrives in item events before an empty terminal response
- **WHEN** upstream emits encrypted items in `response.output_item.done` and an empty terminal `output` array
- **THEN** native SSE preserves the item events and terminal semantics
- **AND** public SSE and collected JSON responses backfill the same encrypted items

#### Scenario: Ciphertext prevents account-neutral replay
- **WHEN** an input payload retains encrypted message or tool-call content after any item-ID projection
- **THEN** account-neutral fresh-replay classification rejects the payload
