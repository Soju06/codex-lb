# replica-operations Delta

## MODIFIED Requirements

### Requirement: Multi-replica deployments require shared PostgreSQL coordination

Running more than one application replica SHALL require: a shared PostgreSQL database through which all cross-replica coordination flows (`scheduler_leader` lease, `bridge_ring_members`, `http_bridge_sessions`, `cache_invalidation`, `sticky_sessions`, `runtime_sentinels`); leader election enabled (`CODEX_LB_LEADER_ELECTION_ENABLED`, which defaults to `true`) so singleton schedulers run on exactly one replica; a unique instance id and a reachable replica-specific advertise URL per replica for bridge owner forwarding; and identical encryption key material mounted on every replica. Explicitly setting `CODEX_LB_LEADER_ELECTION_ENABLED=false` is the single-instance escape hatch that makes every replica treat itself as leader and MUST NOT be used with more than one replica.

#### Scenario: Supported two-replica topology

- **GIVEN** two replicas configured with the same PostgreSQL `CODEX_LB_DATABASE_URL`
- **AND** `CODEX_LB_LEADER_ELECTION_ENABLED` at its default (`true`) on both replicas
- **AND** each replica has a unique bridge instance id with a reachable replica-specific advertise URL
- **AND** both replicas mount the same encryption key file
- **WHEN** both replicas start
- **THEN** exactly one replica acquires the scheduler leader lease and runs singleton schedulers
- **AND** hard-continuity bridge requests landing on the non-owner replica are forwarded to the owner

#### Scenario: Leader election left at its default preserves the singleton guarantee

- **GIVEN** two replicas sharing one PostgreSQL database
- **AND** `CODEX_LB_LEADER_ELECTION_ENABLED` is left at its default (enabled)
- **WHEN** both replicas start
- **THEN** exactly one replica acquires the lease and runs singleton schedulers
- **AND** the operator observes no duplicate upstream polling (usage refresh, automations, retention)
- **AND** explicitly setting `CODEX_LB_LEADER_ELECTION_ENABLED=false` is the single-instance escape hatch that makes every replica treat itself as leader and run singleton schedulers N-fold

### Requirement: SQLite deployments are single-process

SQLite database backends SHALL be operated with exactly one application process; multi-process and multi-replica SQLite deployments — including `uvicorn --workers N` and sharing one SQLite file over a network volume — are unsupported. On SQLite the leader lease is NOT bypassed: it is arbitrated in the database through the same atomic conditional upsert used on PostgreSQL, so when more than one process is pointed at one SQLite file exactly one process wins the lease and the others observe rowcount 0 and remain followers. Multi-process SQLite remains unsupported for other reasons (single-writer contention), and the database rate limiter's cross-process atomicity is likewise unaffected.

#### Scenario: Two leader elections over one SQLite file arbitrate in the database

- **GIVEN** two `LeaderElection` instances pointed at the same SQLite database
- **WHEN** both attempt to acquire the leader lease while no unexpired lease exists
- **THEN** exactly one acquisition wins the lease
- **AND** the other observes rowcount 0 and remains a follower because SQLite arbitrates the lease in the database rather than bypassing it

#### Scenario: Operator scales a SQLite deployment

- **GIVEN** a deployment using a SQLite `CODEX_LB_DATABASE_URL`
- **WHEN** the operator wants more than one application process or replica
- **THEN** the supported path is migrating to a shared PostgreSQL database, not sharing the SQLite file
