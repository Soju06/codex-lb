## ADDED Requirements

### Requirement: Controlled interruptions use cancellation presentation

Request Logs MUST expose retained legacy rows with status `error` and code `interrupted` as public status `cancelled`. The cancellation filter and listing total MUST include these rows, and the error filter MUST exclude them. The existing cancelled label and styling MUST be used, preserving the interruption reason and usage details. Newly persisted controlled interruptions MUST use the existing cancelled accounting rules. Historical aggregate buckets MUST remain unchanged.

#### Scenario: A legacy interruption is listed as cancelled

- **GIVEN** one retained `error/interrupted` row and one genuine error
- **WHEN** Request Logs is filtered by cancelled or error
- **THEN** each filter returns only its matching row with total one
- **AND** the legacy interruption exposes status `cancelled` and reason `interrupted`

#### Scenario: New interruptions do not inflate errors

- **GIVEN** one newly persisted controlled interruption and one genuine error
- **WHEN** dashboard metrics aggregate these rows
- **THEN** there is one cancellation and one error out of two requests
