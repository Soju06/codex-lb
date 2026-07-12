# scheduler-coordination

## ADDED Requirements

### Requirement: Singleton schedulers gate on the shared leader lease

The usage-refresh, api-key limit reset, model refresh, sticky-session cleanup, quota planner, auth guardian, and automations schedulers MUST execute leader-gated work only after acquiring the `scheduler_leader` lease via the leader election's `run_if_leader` gate.

#### Scenario: Two replicas tick concurrently

- **GIVEN** two replicas share one database
- **WHEN** the same singleton scheduler ticks concurrently on both replicas
- **THEN** exactly one replica executes the tick body
- **AND** the other replica skips the tick without side effects

#### Scenario: Lease acquisition errors

- **GIVEN** the database is unreachable during lease acquisition
- **WHEN** a scheduler ticks
- **THEN** the replica treats itself as non-leader and skips the tick
- **AND** the scheduler retries acquisition on its next tick

### Requirement: Lease acquisition is atomic on both database backends

Lease acquisition SHALL be a single conditional upsert on the `scheduler_leader` row (`id = 1`) that takes over only when the lease is expired or already held by the caller, with the winner determined from the statement's affected rowcount. On PostgreSQL and on SQLite alike the lease SHALL be arbitrated in the database; there MUST be no backend that bypasses arbitration. Backend selection MUST derive from the engine dialect, not from the database URL text.

#### Scenario: Two processes share one SQLite file

- **GIVEN** two processes open the same SQLite database file
- **WHEN** both call `try_acquire` while no unexpired lease exists
- **THEN** exactly one process wins the lease
- **AND** the other observes rowcount 0 and remains a follower

#### Scenario: PostgreSQL URL containing the substring "sqlite"

- **GIVEN** a PostgreSQL database URL whose credentials contain the substring "sqlite"
- **WHEN** the leader election selects its SQL flavor
- **THEN** the PostgreSQL arbitration path is used because selection derives from the engine dialect

### Requirement: Lease expiry is evaluated in a single clock domain

On PostgreSQL both the stored expiry (`now() + TTL`) and the takeover predicate (`expires_at < now()`) MUST be computed on the database clock so that inter-replica wall-clock skew cannot steal an unexpired lease. On SQLite (single host by construction) the host clock MAY be used, bound consistently on both sides of the comparison by the same writer.

#### Scenario: Acquiring replica's wall clock is ahead

- **GIVEN** a PostgreSQL deployment where a follower's wall clock is 45 seconds ahead of the leader's
- **AND** the leader holds an unexpired lease
- **WHEN** the follower calls `try_acquire`
- **THEN** the lease is not stolen because expiry is evaluated against the database clock

### Requirement: Leaders renew the lease while gated work runs and demote on loss

While leader-gated work executes, the lease holder MUST renew the lease at an interval no greater than one third of the TTL. Renewal MUST verify that the renewal UPDATE affected a row; an affected rowcount of 0 MUST demote the holder and cancel the in-flight gated work, bounding leader overlap to at most one renew interval plus cancellation latency. Two consecutive renewal errors MUST demote the holder likewise.

#### Scenario: Gated work outlives the TTL

- **GIVEN** a leader whose gated task runs longer than the lease TTL
- **AND** renewal keeps succeeding
- **WHEN** a follower calls `try_acquire` during the task
- **THEN** the follower does not acquire the lease

#### Scenario: Lease is taken over mid-task

- **GIVEN** a leader running a gated task
- **WHEN** the lease row is taken over by another holder
- **THEN** the old leader's renewal observes rowcount 0
- **AND** the old leader cancels the in-flight task within one renew interval
- **AND** the old leader marks itself non-leader

### Requirement: Lease is released on graceful shutdown

On lifespan shutdown, after all schedulers are stopped, the process MUST delete the `scheduler_leader` row it holds (matching its own leader id). Release failure MUST NOT block or fail shutdown; the lease then simply expires after the TTL.

#### Scenario: Leader shuts down cleanly

- **GIVEN** a two-replica deployment where the leader begins graceful shutdown
- **WHEN** the leader's lifespan teardown completes
- **THEN** the lease row is deleted
- **AND** the surviving replica acquires the lease on its next tick without waiting for TTL expiry

#### Scenario: Release fails during shutdown

- **GIVEN** the database is unreachable during shutdown
- **WHEN** the lease release fails or times out
- **THEN** shutdown proceeds
- **AND** followers acquire the lease after the TTL expires

### Requirement: Leader election defaults and configuration

`leader_election_enabled` SHALL default to true. `leader_election_ttl_seconds` SHALL default to 60 and MUST reject values below 5. Disabling leader election MUST cause every replica to treat itself as leader (single-instance escape hatch), and this consequence is documented in the capability context.

#### Scenario: Fresh two-replica deployment with default configuration

- **GIVEN** two replicas share one PostgreSQL database with default environment
- **WHEN** singleton schedulers tick
- **THEN** only one replica runs singleton scheduler work

#### Scenario: TTL below the minimum

- **GIVEN** `CODEX_LB_LEADER_ELECTION_TTL_SECONDS=2`
- **WHEN** settings are loaded
- **THEN** validation fails
