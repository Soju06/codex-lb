## MODIFIED Requirements

### Requirement: Idempotent migration behavior across DB states
The migration chain SHALL remain idempotent for fresh databases and partially migrated legacy databases, including backfills that derive canonical identifiers from revision-local mappings instead of mutable runtime configuration.

#### Scenario: Additional usage quota-key backfill uses revision-local canonical mapping
- **GIVEN** `additional_usage_history` rows created before the `quota_key` column exists
- **AND** the deployment overrides the runtime additional quota registry file
- **WHEN** the migration backfills `quota_key`
- **THEN** it resolves each row through the alias snapshot versioned with that migration revision
- **AND** migrated rows are stored under the stable canonical key defined by that revision
