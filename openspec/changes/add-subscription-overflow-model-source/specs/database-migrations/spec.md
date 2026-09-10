## ADDED Requirements

### Requirement: Subscription overflow schema

The database SHALL persist the subscription-exhaustion overflow designation and pin state as ORM metadata and a single Alembic revision, `20260908_000000_add_subscription_overflow`, that sits on the current head and produces the same logical schema on SQLite and PostgreSQL. The revision MUST add two nullable `dashboard_settings` columns, `subscription_overflow_source_id` (string, no foreign key) and `subscription_overflow_drain_until` (naive UTC timestamp), and MUST create the `model_source_pins` table with `pin_key` as primary key, non-null `kind`, non-null `source_id` without a foreign key, nullable `api_key_id`, non-null timezone-aware `created_at`, `last_seen_at`, `expires_at`, and `purge_at`, and an index named `ix_model_source_pins_purge_at` on `purge_at`. The revision MUST NOT add server defaults, backfill, or seed rows; it MUST be idempotent against a partially applied schema; and its downgrade MUST remove the index, the table, and both columns. Until the overflow routing stages are specified, no request-path component MAY read or write the new columns or table; only dashboard settings workflows specified by the model-source-routing requirements may persist the designation and deadline or read pin counts.

#### Scenario: Existing install is migrated

- **GIVEN** a database at the previous head with a seeded `dashboard_settings` row
- **WHEN** migrations run to head
- **THEN** `dashboard_settings` contains nullable `subscription_overflow_source_id` and `subscription_overflow_drain_until`
- **AND** the existing row keeps both values NULL
- **AND** `model_source_pins` exists, is empty, and carries `ix_model_source_pins_purge_at`

#### Scenario: Fresh SQLite and PostgreSQL databases converge on one head

- **WHEN** an empty SQLite or PostgreSQL database upgrades to head
- **THEN** Alembic reports exactly one head
- **AND** the migration policy and schema drift checks report no violations

#### Scenario: Partially applied schema upgrades idempotently

- **GIVEN** a database below the overflow revision where one settings column or the `model_source_pins` table without its index already exists
- **WHEN** the overflow revision is applied
- **THEN** only the missing column, table, or index is created
- **AND** on PostgreSQL an existing invalid `ix_model_source_pins_purge_at` is dropped and rebuilt as a valid index on `purge_at`

#### Scenario: Downgrade removes the overflow schema

- **WHEN** the overflow revision is downgraded
- **THEN** `ix_model_source_pins_purge_at` and `model_source_pins` are removed
- **AND** both overflow columns are removed from `dashboard_settings`
- **AND** the remaining schema is unchanged
