# replica-operations Specification

## Purpose

Define the supported multi-replica deployment topology contract: what an operator must provision (shared PostgreSQL, leader election, bridge ring identity, shared encryption key) and how startup guardrails, settings concurrency control, and metrics semantics behave across replicas.
## Requirements

### Requirement: Multi-replica deployments require shared PostgreSQL coordination

Running more than one application replica SHALL require: a shared PostgreSQL database through which all cross-replica coordination flows (`scheduler_leader` lease, `bridge_ring_members`, `http_bridge_sessions`, `cache_invalidation`, `sticky_sessions`, `runtime_sentinels`); leader election enabled (`CODEX_LB_LEADER_ELECTION_ENABLED=true`) so singleton schedulers run on exactly one replica; a unique instance id and a reachable replica-specific advertise URL per replica for bridge owner forwarding; and identical encryption key material mounted on every replica.

#### Scenario: Supported two-replica topology

- **GIVEN** two replicas configured with the same PostgreSQL `CODEX_LB_DATABASE_URL`
- **AND** `CODEX_LB_LEADER_ELECTION_ENABLED=true` on both replicas
- **AND** each replica has a unique bridge instance id with a reachable replica-specific advertise URL
- **AND** both replicas mount the same encryption key file
- **WHEN** both replicas start
- **THEN** exactly one replica acquires the scheduler leader lease and runs singleton schedulers
- **AND** hard-continuity bridge requests landing on the non-owner replica are forwarded to the owner

#### Scenario: Leader election left at its default disables the singleton guarantee

- **GIVEN** two replicas sharing one PostgreSQL database
- **AND** `CODEX_LB_LEADER_ELECTION_ENABLED` is left at its default (disabled)
- **WHEN** both replicas start
- **THEN** every replica treats itself as leader and singleton schedulers run N-fold
- **AND** the operator observes duplicate upstream polling (usage refresh, automations, retention) until leader election is enabled

### Requirement: SQLite deployments are single-process

SQLite database backends SHALL be operated with exactly one application process; multi-process and multi-replica SQLite deployments — including `uvicorn --workers N` and sharing one SQLite file over a network volume — are unsupported. On SQLite the leader lease is bypassed and every process treats itself as leader; the database rate limiter's cross-process atomicity is unaffected by this bypass.

#### Scenario: Two leader elections over one SQLite file both acquire

- **GIVEN** two `LeaderElection` instances pointed at the same SQLite database
- **WHEN** both attempt to acquire the leader lease
- **THEN** both acquisitions succeed because SQLite bypasses the lease
- **AND** this documents why running more than one process on SQLite duplicates singleton schedulers

#### Scenario: Operator scales a SQLite deployment

- **GIVEN** a deployment using a SQLite `CODEX_LB_DATABASE_URL`
- **WHEN** the operator wants more than one application process or replica
- **THEN** the supported path is migrating to a shared PostgreSQL database, not sharing the SQLite file

### Requirement: Startup verifies encryption-key consistency against the shared database

At startup, after schema readiness, each replica SHALL compute a fingerprint of its encryption key and atomically stamp it into `runtime_sentinels` (insert-if-absent), then compare its local fingerprint against the stored sentinel. When `CODEX_LB_ENCRYPTION_KEY_FINGERPRINT_MODE=enforce` (the default), a replica whose fingerprint differs from the stored sentinel SHALL refuse to start with an error naming both fingerprint prefixes and remediation steps; `warn` mode SHALL log an ERROR and continue; `off` SHALL disable the check.

#### Scenario: First boot stamps the sentinel

- **GIVEN** an empty `runtime_sentinels` table
- **WHEN** a replica starts
- **THEN** it stamps `sha256` of its encryption key as the `encryption_key_fingerprint` sentinel and starts normally

#### Scenario: Matching replica starts

- **GIVEN** a stamped `encryption_key_fingerprint` sentinel
- **WHEN** a second replica with the same encryption key starts
- **THEN** the fingerprint comparison passes and startup proceeds

#### Scenario: Divergent-key replica refuses to start in enforce mode

- **GIVEN** a stamped `encryption_key_fingerprint` sentinel
- **AND** `CODEX_LB_ENCRYPTION_KEY_FINGERPRINT_MODE` is `enforce`
- **WHEN** a replica with a different encryption key starts
- **THEN** startup fails with an error naming both fingerprint prefixes
- **AND** the error names the remediation (mount the shared key; after an intentional rotation, delete the sentinel row or set the mode to `warn`)

#### Scenario: Divergent-key replica continues in warn mode

- **GIVEN** a stamped `encryption_key_fingerprint` sentinel
- **AND** `CODEX_LB_ENCRYPTION_KEY_FINGERPRINT_MODE=warn`
- **WHEN** a replica with a different encryption key starts
- **THEN** an ERROR is logged and startup continues

#### Scenario: Concurrent first boot of two divergent replicas

- **GIVEN** an empty `runtime_sentinels` table
- **WHEN** two replicas with different encryption keys run the startup check concurrently
- **THEN** exactly one replica stamps the sentinel
- **AND** the other replica's comparison fails against the stamped value

### Requirement: Dashboard settings updates are optimistically locked

The dashboard settings row SHALL carry a monotonically increasing `version` incremented on every persisted ORM update, and full-row updates SHALL apply only when the version still matches the value read by the writer. The version check SHALL run for every accepted `PUT /api/settings`, including a save whose payload changes no field, so a stale writer cannot bypass the conflict guard by submitting an unchanged form. `GET`/`PUT /api/settings` responses SHALL expose `version`; the `PUT` payload MAY include `expectedVersion`, and a stale `expectedVersion` SHALL yield 409 before any write. Internal single-field writers (dashboard auth credential and TOTP mutations) SHALL retry on a version conflict rather than fail.

#### Scenario: Concurrent settings writers race

- **WHEN** two writers (any replicas or sessions) that read the same settings version race on `PUT /api/settings`
- **THEN** exactly one commit succeeds
- **AND** the loser receives 409 with code `settings_conflict` and no partial write

#### Scenario: Stale expectedVersion is rejected before any write

- **GIVEN** a `PUT /api/settings` payload carrying `expectedVersion` older than the current row version
- **WHEN** the update is submitted
- **THEN** the response is 409 with code `settings_conflict`
- **AND** no settings field is modified

#### Scenario: Writer committing between the version check and the update still loses

- **GIVEN** a `PUT /api/settings` request whose `expectedVersion` matched the row when the handler read it
- **WHEN** another writer commits a settings update before the first request's write is applied
- **THEN** the first request's write is rejected with 409 and code `settings_conflict`
- **AND** the interleaved writer's committed fields are not reverted

#### Scenario: Stale no-op save still enforces the version check

- **GIVEN** a `PUT /api/settings` whose payload assigns every field to the value the writer's own (stale) row already holds
- **WHEN** another writer commits a settings update before the no-op save is applied
- **THEN** the no-op save is rejected with 409 and code `settings_conflict`
- **AND** the interleaved writer's committed fields are not reverted

#### Scenario: Internal credential writer retries through a conflict

- **GIVEN** a dashboard-auth credential mutation whose session read the settings row before a concurrent settings update committed
- **WHEN** the credential mutation commits and hits a version conflict
- **THEN** it re-reads the fresh row, re-applies the mutation, and succeeds without surfacing an error

### Requirement: Metrics endpoint semantics are multi-process aware

WHEN metrics are enabled with `PROMETHEUS_MULTIPROC_DIR` set, the scrape registry SHALL aggregate counters across worker processes; WHEN metrics are enabled without `PROMETHEUS_MULTIPROC_DIR` and the standalone metrics port bind fails because another process already holds it, the losing process SHALL log an ERROR stating that `/metrics` reflects only one worker's counters and that `PROMETHEUS_MULTIPROC_DIR` is required for multi-worker aggregation. Multi-host deployments SHALL scrape each replica individually; counters are per-replica and scraping through a load-balanced VIP is unsupported.

#### Scenario: Multi-worker bind conflict without multiproc dir logs an ERROR

- **GIVEN** metrics are enabled and `PROMETHEUS_MULTIPROC_DIR` is not set
- **WHEN** a worker's standalone metrics server fails to bind because another process holds the port
- **THEN** the worker logs an ERROR naming the port and the `PROMETHEUS_MULTIPROC_DIR` remediation
- **AND** the worker keeps serving application traffic

#### Scenario: Multiproc mode keeps benign bind handling

- **GIVEN** metrics are enabled and `PROMETHEUS_MULTIPROC_DIR` is set
- **WHEN** a worker loses the metrics-port bind race
- **THEN** the worker logs at INFO level that another worker serves metrics
