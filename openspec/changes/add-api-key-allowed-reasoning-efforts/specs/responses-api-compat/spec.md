## ADDED Requirements

### Requirement: API-key reasoning allowlists reject disallowed explicit efforts

When an authenticated API key has a non-null
`allowedReasoningEfforts` policy, the proxy MUST derive the client-selected
effort from an explicit `reasoning.effort` or a supported model alias before
performing wire-level normalization. Policy values remain exact client-plane
choices: `xhigh` does not authorize `high`, and `ultra` does not authorize
`max`, even where their downstream wire forms coincide. If the client-selected
effort is not in the policy, the
proxy MUST reject the request before quota reservation, account or source
selection, or upstream dispatch. The rejection MUST use HTTP 403, OpenAI error type `permission_error`, code
`reasoning_effort_not_allowed`, and parameter `reasoning.effort`.

The policy MUST apply to Responses, compact Responses, and WebSocket Responses
requests. Its WebSocket error event MUST preserve the same error code and
parameter. It MUST evaluate the client-plane effort before existing
unsupported-effort fallback and `ultra` to `max` upstream-wire aliasing. A
request that omits an effort MUST retain current default behavior.

#### Scenario: Reject max before upstream dispatch

- **GIVEN** an API key with
  `allowedReasoningEfforts: ["minimal", "low", "medium", "high", "xhigh"]`
- **WHEN** a Responses request explicitly supplies `reasoning.effort: "max"`
- **THEN** the proxy returns `403` with code `reasoning_effort_not_allowed`
- **AND** no API-key quota reservation or upstream request is created

#### Scenario: Alias effort is evaluated as the client-selected value

- **GIVEN** an API key with `allowedReasoningEfforts: ["low", "medium"]`
- **WHEN** a client sends the model alias `gpt-5.6-sol-xhigh`
- **THEN** the proxy rejects the request with code `reasoning_effort_not_allowed`
- **AND** does not forward a request upstream

#### Scenario: Omitted effort remains compatible

- **GIVEN** an API key with `allowedReasoningEfforts: ["low", "medium"]`
- **WHEN** a Responses request omits `reasoning.effort` and uses no effort alias
- **THEN** the proxy does not add or replace a reasoning effort
- **AND** the request continues through the existing route
