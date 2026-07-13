## ADDED Requirements

### Requirement: Legacy default dashboard session TTL migration

The migration for this change MUST update `dashboard_settings.dashboard_session_ttl_seconds` from `43200` to `31536000` only for rows that still carry the legacy default value. Rows with any customized value MUST remain unchanged.

#### Scenario: Legacy default row migrates to 1 year

- **GIVEN** a dashboard settings row has `dashboard_session_ttl_seconds = 43200`
- **WHEN** the migration runs
- **THEN** the row has `dashboard_session_ttl_seconds = 31536000`

#### Scenario: Customized row remains unchanged

- **GIVEN** a dashboard settings row has `dashboard_session_ttl_seconds = 7200`
- **WHEN** the migration runs
- **THEN** the row still has `dashboard_session_ttl_seconds = 7200`
