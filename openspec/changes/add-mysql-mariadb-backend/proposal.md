# Add MySQL/MariaDB backend support

## Why

codex-lb persists to PostgreSQL, with SQLite for embedded/test use. Operators who
already run MySQL/MariaDB cannot deploy codex-lb without a second database
engine, and the current branch ("MySQL / MariaDB support") cannot merge as
submitted: it edits 28 released revisions, ships three migrations in one window,
alters PostgreSQL columns under `ACCESS EXCLUSIVE` at startup, builds
`request_logs` indexes without `CONCURRENTLY`, sizes MySQL `VARCHAR`s from
column-name heuristics, and limits the test suite's schema reuse to MariaDB
while CI runs `mysql:8.4`.

## What Changes

Add MySQL/MariaDB as a supported schema backend, delivered in reviewable steps
rather than one branch:

1. **This proposal** — the contract change that a second backend implies.
2. **Backend-neutral prerequisites** — epoch-second widening (with an online
   PostgreSQL plan) and any DDL-policy adjustments, as their own change.
3. **Dialect layer + MySQL DDL** — `dialect_sql`/`migration_indexes` helpers,
   MySQL-safe DDL for fresh databases, and the test suite's schema strategy, with
   a MySQL + MariaDB leg that is not a required gate (nightly or opt-in).
4. **request_logs performance indexes** — separately, with `CONCURRENTLY` and
   invalid-index repair on PostgreSQL.

## Capabilities

### New Capabilities

- `database-backend`: the contract for which engines a deployment may use, how
  migrations stay engine-agnostic, and what evidence a backend must provide
  (migration check, schema drift, request-path smoke).

### Modified Capabilities

None: this change only defines the backend contract; the port's behavioural
changes are handled by the steps above.

## Impact

- Schema: MySQL/MariaDB DDL must reach head from an empty database with the
  same `migration_policy=ok` / `schema_drift=none` evidence as PostgreSQL.
- Migrations: released revisions are not edited; engine-specific DDL lives in
  helpers or in new revisions, and PostgreSQL statements keep their online
  (non-blocking) form.
- CI: the MySQL leg must complete within the job timeout on the CI service
  (`mysql:8.4`); MariaDB is exercised by a non-required leg.
- Documentation: this repository's deployment docs name the supported engines.
