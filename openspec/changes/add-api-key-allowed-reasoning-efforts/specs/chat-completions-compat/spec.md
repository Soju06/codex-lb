## ADDED Requirements

### Requirement: Chat Completions shares API-key reasoning allowlist enforcement

Before Chat Completions traffic selects a subscription account or an external
model source, the service MUST convert reasoning controls to the internal
Responses representation and apply the authenticated API key's
`allowedReasoningEfforts` policy. A rejected effort MUST produce the same
OpenAI-compatible `403` `reasoning_effort_not_allowed` result as a native
Responses request and MUST NOT call the external source.
The `thinking` string alias MUST recognize every selectable effort, including
`minimal`, before allowlist evaluation.
A snake-case `reasoning_effort` MUST still participate in authorization when a
separate `reasoning` object contains only metadata such as `summary`.
An inactive `thinking` control MUST NOT mask a separate enabled reasoning
alias during authorization.
Reasoning metadata inside `thinking` MUST be merged with enabled controls before
allowlist evaluation and MUST NOT hide their implicit `medium` effort.

After a source-routed Chat Completions request passes the policy, any accepted
`ultra` value MUST use the upstream wire value `max` regardless of whether the
client expressed it through `reasoning_effort`, `reasoningEffort`,
`reasoning.effort`, or `thinking`. If several reasoning spellings conflict,
every retained outbound spelling MUST be aligned to the authorized client-plane
effort. Other allowed client-plane efforts MUST remain unchanged for the
external source. A sole `enable_thinking: true` control authorized as `medium`
MUST remain enabled on source egress.
When source selection replaces an effort-bearing model alias with a canonical
source model slug and the client supplied no separate reasoning control, the
service MUST materialize that authorized effort as `reasoning_effort`. This
applies whether the alias came from the client model or the API key's enforced
model.

#### Scenario: Source-routed chat request is rejected before forwarding

- **GIVEN** a source-routed chat model and an API key with
  `allowedReasoningEfforts: ["low", "medium", "high"]`
- **WHEN** a Chat Completions client supplies `reasoning_effort: "ultra"`
- **THEN** the service returns `403` with code `reasoning_effort_not_allowed`
- **AND** the source receives no request

#### Scenario: Minimal thinking alias is evaluated before forwarding

- **GIVEN** a source-routed chat model and an API key that allows only `low`
- **WHEN** a Chat Completions client supplies `thinking: "minimal"`
- **THEN** the service returns `403` with code `reasoning_effort_not_allowed`
- **AND** the source receives no request

#### Scenario: Reasoning metadata does not mask snake-case effort

- **GIVEN** a source-routed chat model and an API key that allows only `low`
- **WHEN** a Chat Completions client supplies `reasoning_effort: "max"` and
  `reasoning: {"summary": "auto"}`
- **THEN** the service returns `403` with code `reasoning_effort_not_allowed`
- **AND** the source receives no request

#### Scenario: Disabled thinking does not mask an enabled alias

- **GIVEN** a source-routed chat model and an API key that allows only `low`
- **WHEN** a Chat Completions client supplies `thinking: false` and
  `enable_thinking: true`
- **THEN** the service evaluates the enabled alias as `medium`
- **AND** returns `403` with code `reasoning_effort_not_allowed`
- **AND** the source receives no request

#### Scenario: Thinking metadata does not mask its enabled state

- **GIVEN** a source-routed chat model and an API key that allows only `low`
- **WHEN** a Chat Completions client supplies
  `thinking: {"summary": "auto", "enabled": true}`
- **THEN** the service evaluates the enabled control as `medium`
- **AND** returns `403` with code `reasoning_effort_not_allowed`
- **AND** the source receives no request

#### Scenario: Source-routed chat aliases use the ultra wire value

- **GIVEN** a source-routed chat model and an API key that allows `ultra`
- **WHEN** a Chat Completions client supplies `thinking: "ultra"`
- **THEN** the source receives `thinking: "max"`

#### Scenario: Source-routed chat preserves an allowed client-plane effort

- **GIVEN** a source-routed chat model and an API key that allows `minimal`
- **WHEN** a Chat Completions client supplies `reasoning_effort: "minimal"`
- **THEN** the source receives `reasoning_effort: "minimal"`

#### Scenario: Source-routed chat preserves an authorized thinking toggle

- **GIVEN** a source-routed chat model and an API key that allows `medium`
- **WHEN** a Chat Completions client supplies only `enable_thinking: true`
- **THEN** the source receives `enable_thinking: true`

#### Scenario: Canonical source retains effort from a model alias

- **GIVEN** a source registered for `gpt-5.6-sol` and an API key that allows
  `xhigh`
- **WHEN** a Chat Completions client requests `gpt-5.6-sol-xhigh` without a
  separate reasoning control
- **THEN** the source receives model `gpt-5.6-sol`
- **AND** it receives `reasoning_effort: "xhigh"`

#### Scenario: Canonical source retains effort from an enforced model alias

- **GIVEN** a source registered for `gpt-5.6-sol` and an API key that enforces
  `gpt-5.6-sol-xhigh` and allows `xhigh`
- **WHEN** a Chat Completions client requests canonical `gpt-5.6-sol` without a
  separate reasoning control
- **THEN** the source receives model `gpt-5.6-sol`
- **AND** it receives `reasoning_effort: "xhigh"`
