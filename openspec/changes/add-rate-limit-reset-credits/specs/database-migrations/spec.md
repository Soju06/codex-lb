## ADDED Requirements

### Requirement: Reset-credit storage is indexed per account

The database migration MUST create a reset-credit table keyed by `(account_id, credit_id)` and indexed for per-account available-count lookups.

#### Scenario: Migration creates indexed child table

- **GIVEN** the migration upgrades from the previous head
- **WHEN** the new schema is applied
- **THEN** the database contains `account_rate_limit_reset_credits`
- **AND** the table has a foreign key to `accounts`
- **AND** the table has a unique key on `(account_id, credit_id)`
- **AND** the table has indexes on `account_id`, `status`, `expires_at`, and `(account_id, status, expires_at)`
