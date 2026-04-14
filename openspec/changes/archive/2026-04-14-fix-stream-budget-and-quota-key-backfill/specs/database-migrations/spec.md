## MODIFIED Requirements

### Requirement: Idempotent migration behavior across DB states
The migration chain SHALL remain idempotent for fresh databases and partially migrated legacy databases, including backfills that derive canonical identifiers from the deployment registry configuration available at upgrade time.

#### Scenario: Additional usage quota-key backfill uses configured canonical mapping
- **GIVEN** `additional_usage_history` rows created before the `quota_key` column exists
- **AND** the deployment overrides the runtime additional quota registry file
- **WHEN** the migration backfills `quota_key`
- **THEN** it resolves each row through the canonical alias mapping configured for that deployment
- **AND** migrated rows are stored under the canonical key that the running app will query immediately after upgrade
