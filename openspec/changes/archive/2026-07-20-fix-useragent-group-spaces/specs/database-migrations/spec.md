## ADDED Requirements

### Requirement: Request-log user-agent groups are backfilled by a portable data migration

The Alembic migration path MUST include a data migration after the current head that recomputes `request_logs.useragent_group` from each existing nonblank `request_logs.useragent` using the canonical shared-parser rule: trim the user-agent, take the content before the first `/`, or use the entire trimmed value when no `/` exists. Rows whose user-agent is null or blank after trimming MUST have `useragent_group = NULL`. The migration MUST produce the same results on SQLite and PostgreSQL and MUST be idempotent. Its downgrade MUST be a no-op because prior derived values are irrecoverable.

#### Scenario: SQLite historical groups are normalized

- **GIVEN** a SQLite database at the migration's parent revision with existing request-log rows
- **WHEN** the migration upgrades to its revision
- **THEN** every row with a nonblank user-agent has the canonical trimmed `useragent_group`
- **AND** rows with a missing or blank user-agent have `useragent_group = NULL`

#### Scenario: PostgreSQL historical groups are normalized

- **GIVEN** a PostgreSQL database at the migration's parent revision with existing request-log rows
- **WHEN** the migration upgrades to its revision
- **THEN** every row with a nonblank user-agent has the canonical trimmed `useragent_group`
- **AND** rows with a missing or blank user-agent have `useragent_group = NULL`

#### Scenario: Reapplying the data migration is harmless

- **GIVEN** the request-log user-agent-group migration has already run
- **WHEN** the same migration operation is applied again or the database is upgraded to head again
- **THEN** the operation succeeds without changing the canonical results or creating duplicate migration state

#### Scenario: Downgrade preserves normalized history

- **GIVEN** the request-log user-agent-group migration has normalized existing rows
- **WHEN** the migration is downgraded
- **THEN** the downgrade performs no data mutation
- **AND** normalized `useragent_group` values remain unchanged
