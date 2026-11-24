# proxy-runtime-observability Delta

## ADDED Requirements

### Requirement: Request logs persist the effective client reasoning effort

For Responses, compact Responses, and WebSocket Responses request-log rows, the
proxy MUST persist on `request_logs.reasoning_effort` the effort the request
pipeline configured, not the raw request-level field. The configured effort is
the last `configuration_update` input item that carries a non-blank
`reasoning.effort` when one is present; otherwise the effort selected by a
supported model alias; otherwise the request-level `reasoning.effort`. When an
API-key policy fixed the effort, the fixed value is the configured value.

Persisted values MUST use the wire-safe form the upstream payload received:
`ultra` is recorded as `max`, and an effort the upstream rejects (currently
`minimal`) is recorded as the fallback value. This keeps new rows comparable
with rows written before the item form existed. When no configured effort is
recorded, the request-level value is persisted unchanged.

#### Scenario: Mid-conversation update is the logged effort

- **WHEN** a Responses request keeps `reasoning.effort: "none"` at request level
  and carries `{"type": "configuration_update", "reasoning": {"effort": "high"}}`
- **THEN** the persisted request-log row stores `reasoning_effort = "high"`

#### Scenario: Last update wins

- **WHEN** a request carries configuration updates for `low` and then `medium`
- **THEN** the persisted request-log row stores `reasoning_effort = "medium"`

#### Scenario: Enforced effort is the logged effort

- **GIVEN** an API key with an enforced reasoning effort
- **WHEN** a Responses request carries a different client effort or a
  configuration update item
- **THEN** the persisted request-log row stores the enforced effort
- **AND** the upstream payload carries the same enforced effort

#### Scenario: Requests without updates keep the request-level value

- **WHEN** a Responses request carries no configuration update item and no
  effort-bearing alias
- **THEN** the persisted request-log row stores the request-level effort
  unchanged

#### Scenario: Client-plane aliases are logged in wire form

- **WHEN** the configured effort is `ultra`
- **THEN** the persisted request-log row stores `reasoning_effort = "max"`
