## ADDED Requirements

### Requirement: Staggered idle warm-up idle threshold column

The `dashboard_settings` table MUST include a `limit_warmup_idle_threshold_percent` column of type `Float`, nullable `False`, with a server default of `1.0`. This column stores the operator-configurable idle threshold for the staggered idle warm-up path, independent from the regular warm-up's `limit_warmup_exhausted_threshold_percent`.

#### Scenario: Column exists after migration

- **GIVEN** the database has been migrated to the latest revision
- **WHEN** the schema is inspected
- **THEN** the `dashboard_settings` table includes `limit_warmup_idle_threshold_percent` as a non-null `Float` column
- **AND** the default value is `1.0`

#### Scenario: Existing rows get the default value

- **GIVEN** a `dashboard_settings` row exists before the migration
- **WHEN** the migration adds the column
- **THEN** the existing row's `limit_warmup_idle_threshold_percent` is `1.0`
