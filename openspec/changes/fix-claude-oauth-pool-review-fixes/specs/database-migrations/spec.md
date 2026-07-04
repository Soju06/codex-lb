# database-migrations Specification (delta)

## ADDED Requirements

### Requirement: Migration up/down/up reversibility

Every Alembic revision introduced by a behavioral change MUST round-trip cleanly: `upgrade(head) → downgrade(base) → upgrade(head)` MUST succeed without raising and MUST leave the schema in a state observably equivalent to a single forward `upgrade(head)`. The downgrade MUST drop CHECK constraints and indexes that reference a column BEFORE dropping the column (e.g. `ck_accounts_provider` before `accounts.provider`), and MUST restore the original column NULL-ability (e.g. `accounts.email NOT NULL`) in the same `batch_alter_table` block as the column drops. A round-trip test MUST exist for the new migrations in `tests/unit/test_db_migrate.py` covering both the SQLite and the Postgres dialects (the Postgres variant only runs when `CODEX_LB_TEST_DATABASE_URL` is a Postgres URL).

#### Scenario: Claude schema round-trips cleanly on SQLite

- **GIVEN** an empty SQLite database
- **WHEN** Alembic runs `upgrade head` then `downgrade base` then `upgrade head` for the Claude schema migrations
- **THEN** the final schema is observably equivalent to a single forward `upgrade head`
- **AND** `inspect_migration_state(url).current_revision` equals the recorded head revision

#### Scenario: Downgrade drops CHECK constraint before column

- **GIVEN** the Claude migration's `20260701_000000_add_claude_account_columns` revision
- **WHEN** Alembic runs `downgrade -1`
- **THEN** the `ck_accounts_provider` CHECK constraint is dropped
- **AND** the `uq_accounts_claude_uuid` partial unique index is dropped
- **AND** the `accounts.provider` column is dropped
- **AND** `accounts.email` is restored to `NOT NULL` in the same `batch_alter_table` block

#### Scenario: Downgrade drops the second migration's CHECK before re-applying

- **GIVEN** the Claude migration's `20260701_010000_enforce_claude_rt_and_codex_email_invariants` revision
- **WHEN** Alembic runs `downgrade -1`
- **THEN** the `ck_accounts_claude_rt_required` CHECK constraint is dropped
- **AND** the `uq_accounts_codex_email` partial unique index is dropped
- **AND** the re-applied upgrade restores both constraints in the same order as a fresh `upgrade head`
