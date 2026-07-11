## ADDED Requirements

### Requirement: Upstream Responses payloads omit client-omitted request fields

The service MUST NOT emit top-level request fields the client omitted onto
upstream Responses payloads when the field's absence is meaningful upstream.
In particular, the proxy MUST NOT synthesize a top-level `"tools": []` from
the request model's default for clients that did not send the `tools` field,
on any upstream transport (websocket `response.create` frames, HTTP-bridge
bodies, and direct HTTP stream requests). An explicit client-sent
`"tools": []` MUST be forwarded as `[]`. `tool_choice` and
`parallel_tool_calls` MUST be forwarded only when the client sent them;
an explicit client-sent `parallel_tool_calls: false` MUST reach upstream.
The OpenAI-compatible `/v1/responses` conversion MUST propagate `tools`
omission into the native request so both routes behave identically.

#### Scenario: Responses Lite request reaches upstream without a tools key

- **WHEN** a `/backend-api/codex/responses` request omits top-level `tools`
  and carries its tool bundle in an `additional_tools` input item
- **THEN** the upstream websocket `response.create` frame contains no
  top-level `tools` key
- **AND** the HTTP-bridge request body contains no top-level `tools` key

#### Scenario: Explicit empty tools array is forwarded

- **WHEN** a client sends `"tools": []` explicitly
- **THEN** the upstream payload contains `"tools": []`

#### Scenario: Unset optional tool fields stay absent

- **WHEN** a client omits `tool_choice` and `parallel_tool_calls`
- **THEN** the upstream payload contains neither field

### Requirement: Client tool entries are forwarded byte-preserved

The service MUST forward client-sent top-level `tools` entries to upstream
byte-preserved: the tool array order, per-object key order, unknown keys
(including unknown tool types such as `namespace` entries and non-standard
schema markers), and array-value order (for example `parameters.required`)
MUST reach upstream exactly as the client sent them. Tool canonicalization
(array sorting and recursive key sorting) MUST be used only for prompt-cache
affinity and observability hashing and MUST NOT mutate the outgoing payload.
The affinity/observability hash MUST remain insensitive to tool array order
and object key order.

#### Scenario: Reserved namespace tool survives byte-identical

- **WHEN** a client sends top-level `tools` containing a reserved
  `{"type": "namespace", "name": "collaboration", ...}` entry with nested
  function entries, `strict: false`, unknown property markers, and a
  non-alphabetical `required` array
- **THEN** the upstream `response.create` frame serializes that `tools` array
  byte-identical to the client's serialization

#### Scenario: Affinity hash ignores tool ordering

- **WHEN** two requests differ only in tool array order or tool object key
  order
- **THEN** their tools affinity/observability hash is identical
