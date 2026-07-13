## ADDED Requirements

### Requirement: Dashboard settings persistence

The database SHALL persist dashboard settings, including weekly pace working days and the weekly pace gap smoothing window.

#### Scenario: Existing installs receive weekly pace smoothing default
- **WHEN** an existing database is migrated
- **THEN** `dashboard_settings.weekly_pace_smoothing_minutes` exists
- **AND** existing rows use a default smoothing window of 30 minutes
