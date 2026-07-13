## ADDED Requirements

### Requirement: Accounts have server-owned Codex installation ids

The `accounts` table MUST store a non-null `codex_installation_id` for every
account. New account rows MUST receive a generated UUID value. Existing account
rows MUST be backfilled during migration.

#### Scenario: Existing accounts are backfilled

- **GIVEN** an existing database has account rows without
  `codex_installation_id`
- **WHEN** migrations upgrade to the new revision
- **THEN** each existing account row has a non-empty UUID

#### Scenario: New accounts receive an installation id

- **WHEN** a new account row is created by the application
- **THEN** `codex_installation_id` is populated without trusting client input
