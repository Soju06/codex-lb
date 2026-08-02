## ADDED Requirements

### Requirement: Chat compatibility may opt into a local reasoning-effort override

The proxy MUST leave existing request behavior unchanged unless an
`officeai-reasoning.json` control file exists beside the active local SQLite
database. When present, the proxy MUST
apply the selected effort only to `/v1/chat/completions` requests that do not
already provide an explicit reasoning effort. Missing, unreadable, or invalid
control files MUST leave requests unchanged.

#### Scenario: Missing effort receives configured override

- **GIVEN** the OfficeAI reasoning config file is enabled with effort `high`
- **AND** its API-key prefix matches the authenticated request
- **AND** a `/v1/chat/completions` request carries no explicit reasoning effort
- **WHEN** the proxy converts the request for upstream delivery
- **THEN** `reasoning.effort` MUST be `high`

#### Scenario: Explicit caller effort is preserved

- **GIVEN** the OfficeAI reasoning config file selects `maximum`
- **AND** a `/v1/chat/completions` request explicitly carries
  `reasoning.effort=low`
- **WHEN** the proxy converts the request
- **THEN** `reasoning.effort` MUST remain `low`

#### Scenario: Maximum resolves to a wire-safe model level

- **GIVEN** the OfficeAI reasoning config file selects `maximum`
- **AND** the selected model advertises ordered reasoning levels ending in
  `ultra`
- **WHEN** the proxy converts a request without an explicit effort
- **THEN** the upstream effort MUST be `max`
- **AND** the client-only `ultra` value MUST NOT be sent upstream

#### Scenario: API-key enforcement retains final authority

- **GIVEN** the OfficeAI reasoning config selects `maximum`
- **AND** the authenticated API key enforces reasoning effort `medium`
- **WHEN** the proxy applies request policies
- **THEN** the final upstream effort MUST be `medium`
