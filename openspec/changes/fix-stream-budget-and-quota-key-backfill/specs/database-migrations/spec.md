## MODIFIED Requirements

### Requirement: Idempotent migration behavior across DB states
The migration chain SHALL remain idempotent for fresh databases and partially migrated legacy databases, including backfills that derive canonical identifiers from runtime configuration.

#### Scenario: Additional usage quota-key backfill uses configured registry canonicalization
- **GIVEN** `additional_usage_history` rows created before the `quota_key` column exists
- **AND** the deployment overrides the additional quota registry file with a custom canonical key or alias mapping
- **WHEN** the migration backfills `quota_key`
- **THEN** it resolves each row through the same configured canonicalization path that runtime routing uses
- **AND** migrated rows are stored under the canonical key that later runtime queries will read
