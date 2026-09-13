## Why

Startup stamps two insert-if-absent sentinels into `runtime_sentinels`: the
encryption-key fingerprint and the hard-sticky outage-grace marker. Both are
the first statement of their transaction on a startup-fresh connection, so
neither can be protected by `BEGIN IMMEDIATE` (there is no earlier read to
upgrade) and either can lose SQLite's single writer slot on a loaded host.
The fingerprint stamp already retries; the outage-grace seed has no lock
tolerance at all, and it is the failing statement in most of the CI hits on
issue #1949. Worse, both mechanisms behind `database is locked` — an instant
`SQLITE_BUSY_SNAPSHOT` and a `SQLITE_BUSY` returned only after the full busy
timeout — produce the identical message, so today's failures cannot be told
apart after the fact.

## What Changes

- Share one bounded lock-retry budget (three retries, under a second in
  total) between both startup sentinel writes, rolling back the dirty
  transaction between attempts.
- Preserve each call site's existing failure outcome: a lock that outlives
  the budget still fails startup, and non-lock `OperationalError`s still
  propagate unretried so the seed's missing-table degrade is untouched.
- Log the driver's `sqlite_errorname` and the elapsed time when a budget is
  exhausted (WARNING) and on each retry (DEBUG), so the next occurrence
  classifies itself.

## Capabilities

### Modified Capabilities
- database-backends: startup sentinel writes tolerate, and name, transient SQLite lock contention.

## Impact

Two startup call sites (`app/core/config/key_fingerprint.py`,
`AccountsRepository.seed_hard_sticky_outage_grace_on_startup`) plus a shared
`app/db/sqlite_lock_retry.py` helper. No configuration, schema, or
request-path change.
