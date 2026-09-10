## ADDED Requirements

### Requirement: Stage the reset-threshold column without activating it

The compatibility migration MUST add a nullable `Float` column named
`limit_warmup_reset_threshold_percent` to `dashboard_settings` without a
server default, backfill, or runtime activation. The existing
`limit_warmup_exhausted_threshold_percent` column MUST remain non-null with its
`99.0` default and its existing values unchanged.

#### Scenario: Existing rows retain the legacy threshold

- **GIVEN** a database row whose legacy exhausted threshold is `99.0` or an
  explicitly configured positive value
- **WHEN** the compatibility migration is upgraded
- **THEN** the new reset-threshold column is nullable and `NULL` for that row
- **AND** the legacy threshold value remains unchanged

#### Scenario: Fresh dashboard settings can omit the staged value

- **GIVEN** the compatibility migration has been upgraded
- **WHEN** a dashboard settings row is created without an activation value
- **THEN** the row is valid with a `NULL` reset-threshold column
- **AND** the legacy threshold still has its `99.0` default

#### Scenario: Downgrade removes only the staged column

- **GIVEN** the compatibility migration has been upgraded
- **WHEN** it is downgraded
- **THEN** `limit_warmup_reset_threshold_percent` is removed
- **AND** `limit_warmup_exhausted_threshold_percent` and its stored values remain

