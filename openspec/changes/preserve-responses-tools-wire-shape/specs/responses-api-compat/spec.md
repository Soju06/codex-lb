# responses-api-compat — Delta

## ADDED Requirements

### Requirement: Responses tools preserve client field presence and structure

When a Responses client omits the top-level `tools` field, the service MUST
omit that field from upstream Responses payloads rather than synthesizing an
empty array. This requirement MUST hold across native HTTP, websocket,
HTTP-to-websocket bridge, multi-instance owner forwarding, OpenAI-compatible
Responses conversion, and model-source Responses egress. When the client
explicitly supplies `tools: []`, the service MUST preserve the explicit empty
array on non-compact Responses paths.

When the client supplies nonempty top-level tools, the service MUST NOT
canonicalize or reorder them on the upstream wire. After any existing
endpoint-specific compatibility filtering, the service MUST preserve the
relative array order and each surviving tool definition's nested mapping key
order. The service MAY canonicalize a detached copy for an order-insensitive
observability hash, but that operation MUST NOT mutate the validated request or
its upstream payload. Compact requests remain governed by their existing rule
that removes top-level tools.

#### Scenario: Responses Lite request omits top-level tools

- **WHEN** a Responses Lite request carries its tools in an
  `additional_tools` input item and omits top-level `tools`
- **THEN** the upstream HTTP or websocket payload has no top-level `tools` key
- **AND** the `additional_tools` item remains unchanged

#### Scenario: Explicit empty tools remains explicit

- **WHEN** a non-compact Responses client explicitly sends `tools: []`
- **THEN** the upstream payload contains `tools: []`

#### Scenario: Reserved tool definition remains wire-faithful

- **WHEN** a client supplies a top-level reserved function tool definition
- **THEN** the upstream payload retains the tool list order and nested mapping
  key order supplied by the client
- **AND** request-shape hashing does not mutate the validated or forwarded tool
  definition

#### Scenario: Owner forwarding preserves an omitted tools field

- **WHEN** an HTTP bridge request without top-level tools is forwarded to its
  owning proxy instance
- **THEN** the internal forward body omits top-level `tools`
- **AND** the receiving instance retains the field as unset when preparing its
  upstream request
