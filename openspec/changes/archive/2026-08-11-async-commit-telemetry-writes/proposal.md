# Relax commit durability for telemetry writes on PostgreSQL

## Why

On the production PostgreSQL deployment (15 accounts, 4.5M request logs), 93% of slow queries over 500 ms are write-path fsync waits: `INSERT request_logs` (667 calls / 30.8 s per 10 min), `INSERT api_key_usage_reservations` (1,612 calls / 25.7 s), and the settlement `UPDATE api_keys SET last_used_at` (616 calls / 44.6 s). For rows that are pure observability (request logs, usage-history samples), losing the final few hundred milliseconds of unflushed WAL after a server crash changes nothing about accounting semantics — full durability buys those transactions nothing.

Reservation accounting is different (adversarial review P1 on PR #1628): on external/HA PostgreSQL, a database failover does not kill in-flight application requests. If an acked-but-lost settlement commit is dropped in the failover, the reservation stays `reserved`, the stale-release scheduler later reverses the limit counters and records zero actual usage, and a request that actually completed disappears from token/cost/rate-limit accounting — violating the settlement invariant. So reservation writes must keep full durability.

## What Changes

- Add a shared helper `relax_commit_durability(session)` in `app/db/session.py` that emits `SET LOCAL synchronous_commit = off` inside the current write transaction when the session's dialect is PostgreSQL, and is a no-op otherwise (SQLite durability stays governed by `PRAGMA synchronous`). `SET LOCAL` is transaction-scoped, so PostgreSQL reverts it automatically at COMMIT/ROLLBACK and nothing leaks onto pooled connections.
- Invoke the helper inside the high-frequency append-only telemetry write transactions:
  - `RequestLogsRepository.add_log` (request-log insert),
  - `UsageRepository.add_entry`, `UsageRepository.add_account_snapshot`, and `AdditionalUsageRepository.add_entry` (usage-history appends).
- API-key usage-reservation accounting keeps full durability: `ApiKeysRepository.create_usage_reservation`, `ApiKeysRepository.settle_usage_reservation`, and `ApiKeysRepository.release_stale_usage_reservations` MUST NOT call the helper. An acked-but-lost reservation commit on an HA PostgreSQL failover desynchronizes the reservation ledger from requests that still complete (see Why).
- Configuration writes (account/key/limit/setting mutations, migrations, schedulers' state) are explicitly NOT relaxed and keep full durability.
- No new settings (reduce-settings-surface policy): the behavior is an unconditional application constant of the telemetry write paths.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `database-backends`: add the telemetry-write durability contract — which write transactions qualify as telemetry, the bounded-loss semantics they accept, the requirement that the relaxation is transaction-scoped (`SET LOCAL`) and PostgreSQL-only — and the counterpart requirement that API-key usage-reservation accounting (creation, settlement, stale release) and configuration writes retain full durability.

## Impact

- Affected code: `app/db/session.py` (helper), `app/modules/request_logs/repository.py`, `app/modules/usage/repository.py`; `app/modules/api_keys/repository.py` documents (and tests enforce) that reservation paths stay fully durable.
- Affected tests: new unit test for the helper's dialect guard; new PostgreSQL integration tests proving in-transaction application, automatic revert after COMMIT/ROLLBACK, the outside-transaction WARNING trap, telemetry paths emitting the relaxation, and reservation/configuration writes not emitting it.
- Crash-loss contract: on a PostgreSQL server crash, committed telemetry rows within the final unflushed WAL window — bounded by three times `wal_writer_delay`, up to ~600 ms at the default 200 ms setting — may be lost. Reservation accounting accepts no such loss window. No API change, no frontend change, no migration, no new settings.
