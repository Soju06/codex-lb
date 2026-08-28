# Delta Specification: api-keys

## ADDED Requirements

### Requirement: API-key limit rule identities are unique

The system SHALL reject an API-key create or update payload when it contains
more than one limit rule with the same `(limit_type, limit_window,
model_filter)` identity. Rejection MUST use the typed API-key validation error
and MUST occur before a create request persists an API key or limit row.
The validation message MUST identify the duplicate rule identity.

#### Scenario: Duplicate rules are rejected during creation

- **WHEN** an administrator submits `POST /api/api-keys` with two limit rules
  sharing the same type, window, and model filter
- **THEN** the API returns `400` with `invalid_api_key_payload`
- **AND** no API key or limit row is persisted
