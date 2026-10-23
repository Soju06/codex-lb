# responses-api-compat Delta

## MODIFIED Requirements

### Requirement: API-key reasoning allowlists reject disallowed explicit efforts

When an authenticated API key has a non-null
`allowedReasoningEfforts` policy, the proxy MUST derive the client-selected
effort before performing wire-level normalization. The derived effort is the
last `configuration_update` input item that carries a non-blank
`reasoning.effort` when one is present; otherwise a supported model alias that
selects an effort; otherwise an explicit request-level `reasoning.effort`.
Policy values remain exact client-plane
choices: `xhigh` does not authorize `high`, and `ultra` does not authorize
`max`, even where their downstream wire forms coincide. If the client-selected
effort is not in the policy, the
proxy MUST reject the request before quota reservation, account or source
selection, or upstream dispatch. The rejection MUST use HTTP 403, OpenAI error type `permission_error`, code
`reasoning_effort_not_allowed`, and parameter `reasoning.effort`.

A `configuration_update` input item is the client's most recent selection and
applies from its position onward, so the last such item is the effective one.
Only a JSON object item whose `type` is `configuration_update` and whose
`reasoning.effort` is a non-blank string participates. Item effort values MUST
be trimmed and case-normalized before authorization, exactly like the
request-level field. The item form MUST NOT be rewritten or removed: items are
forwarded verbatim to upstream, and only the derived value drives policy,
logging, and accounting.

Because items are forwarded verbatim, an API key with a non-null
`enforcedReasoningEffort` MUST reject a request whose update item selects a
different effort: rewriting only the request-level field would still let the
item apply upstream. The rejection uses the same HTTP 403,
`reasoning_effort_not_allowed` code and `reasoning.effort` parameter.

The policy MUST apply to Responses, compact Responses, and WebSocket Responses
requests. Its WebSocket error event MUST preserve the same error code and
parameter. It MUST evaluate the client-plane effort before existing
unsupported-effort fallback and `ultra` to `max` upstream-wire aliasing. A
request that omits an effort MUST retain current default behavior.
Applying the policy more than once to the same request, including across a
signed internal HTTP bridge hop, MUST be idempotent and MUST NOT re-authorize
an already-normalized wire value as though it were the original client choice.
Before source-routed Responses traffic is forwarded, accepted reasoning
aliases MUST be aligned with the authorized canonical `reasoning.effort` or
removed so a conflicting alias cannot select a disallowed effort upstream.
Blank alias strings MUST be treated as absent and MUST NOT mask a later
effort-bearing alias during authorization.
Disabled aliases MUST likewise be treated as inactive rather than masking a
separate enabled reasoning alias.
Provider reasoning metadata MUST be merged with enabled controls before effort
authorization instead of masking their implicit `medium` effort.
When no reasoning policy is active, source egress MUST retain existing
provider-shaped reasoning controls and their source-specific fields.
An allowlist MUST also preserve provider-shaped controls that select no effort;
their unrelated fields do not participate in effort authorization.

#### Scenario: Reject max before upstream dispatch

- **GIVEN** an API key with
  `allowedReasoningEfforts: ["minimal", "low", "medium", "high", "xhigh"]`
- **WHEN** a Responses request explicitly supplies `reasoning.effort: "max"`
- **THEN** the proxy returns `403` with code `reasoning_effort_not_allowed`
- **AND** no API-key quota reservation or upstream request is created

#### Scenario: Configuration update effort is authorized as the client-selected value

- **GIVEN** an API key with `allowedReasoningEfforts: ["low", "medium"]`
- **WHEN** a Responses request carries
  `{"type": "configuration_update", "reasoning": {"effort": "medium"}}` as an
  input item and no different request-level effort
- **THEN** the request is authorized and forwarded
- **AND** the item is forwarded verbatim

#### Scenario: Disallowed configuration update effort is rejected before dispatch

- **GIVEN** an API key with `allowedReasoningEfforts: ["low", "medium"]`
- **WHEN** a Responses request carries
  `{"type": "configuration_update", "reasoning": {"effort": "max"}}` as an
  input item
- **THEN** the proxy returns `403` with code `reasoning_effort_not_allowed`
- **AND** no API-key quota reservation or upstream request is created

#### Scenario: Configuration update outranks a model alias

- **GIVEN** an API key with `allowedReasoningEfforts: ["low"]`
- **WHEN** a client sends an effort-bearing model alias that selects `low` and
  also carries
  `{"type": "configuration_update", "reasoning": {"effort": "max"}}`
- **THEN** the proxy rejects the request with code `reasoning_effort_not_allowed`
- **AND** does not forward a request upstream

#### Scenario: Enforced effort conflicts with a configuration update

- **GIVEN** an API key with `enforcedReasoningEffort: "low"`
- **WHEN** a Responses request carries
  `{"type": "configuration_update", "reasoning": {"effort": "high"}}` as an
  input item
- **THEN** the proxy returns `403` with code `reasoning_effort_not_allowed`
- **AND** does not forward a request upstream

An enforced effort cannot be guaranteed while a verbatim item selects a
different one, so the request MUST fail instead of logging the enforced value.

#### Scenario: Alias effort is evaluated as the client-selected value

- **GIVEN** an API key with `allowedReasoningEfforts: ["low", "medium"]`
- **WHEN** a client sends the model alias `gpt-5.6-sol-xhigh`
- **THEN** the proxy rejects the request with code `reasoning_effort_not_allowed`
- **AND** does not forward a request upstream

#### Scenario: Omitted effort remains compatible

- **GIVEN** an API key with `allowedReasoningEfforts: ["low", "medium"]`
- **WHEN** a Responses request omits `reasoning.effort`, uses no effort alias,
  and carries no configuration update item
- **THEN** the proxy does not add or replace a reasoning effort
- **AND** the request continues through the existing route

#### Scenario: Effort-less provider controls remain compatible

- **GIVEN** a source-routed model and an API key with
  `allowedReasoningEfforts: ["low"]`
- **WHEN** a Responses request supplies
  `thinking: {"type": "adaptive", "budget_tokens": 2048}` without an effort
- **THEN** the source receives the original `thinking` object

#### Scenario: Source-routed conflicting alias cannot override policy

- **GIVEN** a source-routed model and an API key with
  `allowedReasoningEfforts: ["low"]`
- **WHEN** a Responses request supplies `reasoning.effort: "low"` and
  `thinking: "max"`
- **THEN** the source receives the canonical `reasoning.effort: "low"`
- **AND** it does not receive the conflicting `thinking` alias

#### Scenario: Blank alias cannot hide a disallowed effort

- **GIVEN** a source-routed model and an API key with
  `allowedReasoningEfforts: ["low"]`
- **WHEN** a Responses request supplies `reasoningEffort: " "` and
  `thinking: "max"`
- **THEN** the service returns `403` with code `reasoning_effort_not_allowed`
- **AND** the source receives no request

#### Scenario: Disabled alias cannot hide an enabled effort

- **GIVEN** a source-routed model and an API key with
  `allowedReasoningEfforts: ["low"]`
- **WHEN** a Responses request supplies `thinking: "disabled"` and
  `enable_thinking: true`
- **THEN** the service evaluates the enabled alias as `medium`
- **AND** returns `403` with code `reasoning_effort_not_allowed`
- **AND** the source receives no request

#### Scenario: Provider metadata cannot hide an enabled effort

- **GIVEN** a source-routed model and an API key with
  `allowedReasoningEfforts: ["low"]`
- **WHEN** a Responses request supplies
  `thinking: {"summary": "auto", "enabled": true}`
- **THEN** the service evaluates the enabled control as `medium`
- **AND** returns `403` with code `reasoning_effort_not_allowed`
- **AND** the source receives no request

#### Scenario: Effort-less provider control survives beside an allowed effort

- **GIVEN** a source-routed model and an API key with
  `allowedReasoningEfforts: ["low"]`
- **WHEN** a Responses request supplies `reasoning.effort: "low"` and
  `thinking: {"type": "adaptive", "budget_tokens": 2048}`
- **THEN** the source receives both the authorized canonical effort and the
  provider control with its unrelated fields intact
