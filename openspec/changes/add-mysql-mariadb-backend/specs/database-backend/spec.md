# database-backend

## ADDED Requirements

### Requirement: Supported engines are an explicit, tested contract

A deployment MUST run on PostgreSQL or SQLite, and MAY run on MySQL/MariaDB once
this change is implemented. Every supported engine MUST be exercised by a
migration check that reaches head from an empty database and reports
`migration_policy=ok` and `schema_drift=none`.

#### Scenario: Fresh database reaches head

- **GIVEN** an empty MySQL/MariaDB database and the application image
- **WHEN** the application boots and runs its migrations
- **THEN** the schema revision equals head and `migration_policy=ok`
- **AND** a subsequent check reports `schema_drift=none`

### Requirement: Migrations stay engine-agnostic and online on PostgreSQL

Schema changes MUST NOT edit released revisions; engine differences MUST live in
new revisions or in shared helpers. Statements that rebuild or lock a large
PostgreSQL table MUST use their online form (`CREATE INDEX CONCURRENTLY`, or a
documented window for `ALTER TABLE`).

#### Scenario: Backfill uses engine-appropriate syntax

- **GIVEN** the cost-backfill migration running on MySQL/MariaDB
- **WHEN** it applies the staged rows to `request_logs`
- **THEN** it uses the join form, not `UPDATE ... FROM`

#### Scenario: Index build does not block inserts

- **GIVEN** a new `request_logs` index on PostgreSQL
- **WHEN** the migration builds it
- **THEN** it is built with `CREATE INDEX CONCURRENTLY`
- **AND** a leftover invalid index from an interrupted build is dropped first

### Requirement: MySQL column lengths follow data semantics

MySQL/MariaDB `VARCHAR` lengths MUST be derived from what a column stores, not
from its name. Client-supplied values MUST have headroom for their documented
maximum, and a value that exceeds the column MUST NOT cause a 1406 failure on a
normal request path.

#### Scenario: Client conversation header

- **GIVEN** a client conversation header longer than 96 characters
- **WHEN** it is stored on MySQL/MariaDB
- **THEN** the write succeeds because the column has documented headroom
