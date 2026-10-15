## ADDED Requirements

### Requirement: Quota planner timezone settings are validated safely

The quota planner settings API MUST trim and validate every non-empty timezone
update using the supported timezone database before persisting any settings from
the request. Unknown or malformed timezone keys MUST return HTTP 400 with the
dashboard error code `invalid_quota_planner`, leaving the stored settings unchanged.
Omitted, null, or whitespace-only timezone updates MUST retain the stored value.
Forecasting and routing MUST use the existing UTC fallback for already stored
unknown or malformed timezone keys without raising a timezone lookup exception.

#### Scenario: Invalid update leaves settings unchanged

- **GIVEN** the planner has valid saved settings
- **WHEN** a settings update includes `/Europe/Stockholm` or an unknown timezone
- **THEN** the API returns HTTP 400 with code `invalid_quota_planner`
- **AND** none of the settings from the rejected request are persisted

#### Scenario: Valid and omitted timezone updates retain compatibility

- **WHEN** an operator saves ` Europe/Stockholm `
- **THEN** the API stores `Europe/Stockholm`
- **AND** subsequent omitted, null, or blank timezone updates retain that value

#### Scenario: Legacy malformed timezone does not interrupt planning

- **GIVEN** a previously stored timezone contains a malformed key
- **WHEN** forecast or cold-account routing costs are evaluated
- **THEN** the evaluation uses UTC and completes without a timezone lookup exception
- **AND** the stored key remains available for operator correction
