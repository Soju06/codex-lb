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

While leader-gated work executes, the lease holder MUST renew the lease at an interval no greater than one third of the TTL. Each renewal attempt MUST be time-boxed to no more than one sixth of the TTL so that a hung database call cannot silently extend leadership; a timed-out attempt counts as a renewal error. Renewal MUST verify that the renewal UPDATE affected a row; an affected rowcount of 0 MUST demote the holder and request cancellation of the in-flight gated work. Two consecutive renewal errors MUST demote the holder likewise, and any renewal error observed after the holder's locally tracked lease deadline (last successful renewal or acquisition plus TTL) has passed MUST demote immediately, so a leader with a hung or unreachable database demotes itself no later than the lease TTL.

After demotion the gate MUST await the cancelled body for at most a bounded grace period and then detach it, so the gate itself stops within one renew interval plus the grace. A body that shields in-flight singleton refresh work (token or usage refresh singleflights) MAY drain that work concurrently with a new leader; this residual overlap is bounded by the underlying operation's own timeout and is documented with its safety argument in the capability context.

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

#### Scenario: Renewal hangs against a dead database

- **GIVEN** a leader whose renewal database calls hang indefinitely
- **WHEN** two consecutive time-boxed renewal attempts fail
- **THEN** the leader demotes itself no later than the lease TTL
- **AND** the in-flight gated task is cancelled

#### Scenario: Body shields in-flight refresh work past cancellation

- **GIVEN** a leader whose gated body is inside a shielded singleton refresh when the lease is lost
- **WHEN** the gate cancels the body and the body keeps draining the shielded work
- **THEN** the gate stops awaiting after the bounded grace period and returns as non-leader
- **AND** the detached body is bounded by the refresh operation's own timeout and its outcome is logged

### Requirement: Lease is released on graceful shutdown

On lifespan shutdown, after all schedulers are stopped, the process MUST delete the `scheduler_leader` row it holds (matching its own leader id). Before deleting the row, release MUST wait a bounded grace for any gated body that was detached still draining shielded singleton work; if such a body is still running after the grace, release MUST skip deleting the row entirely — the lease then expires after its TTL — so a follower cannot acquire the lease while the shutting-down process may still execute leader-gated work. Release failure MUST NOT block or fail shutdown; the lease then simply expires after the TTL.

#### Scenario: Leader shuts down cleanly

- **GIVEN** a two-replica deployment where the leader begins graceful shutdown
- **WHEN** the leader's lifespan teardown completes
- **THEN** the lease row is deleted
- **AND** the surviving replica acquires the lease on its next tick without waiting for TTL expiry

#### Scenario: Shutdown with a detached gated body still draining

- **GIVEN** a leader shutting down while a detached gated body is still draining shielded refresh work
- **WHEN** the release drain grace elapses with the body still running
- **THEN** the lease row is not deleted
- **AND** shutdown proceeds and followers acquire the lease only after the TTL expires

#### Scenario: Release fails during shutdown

- **GIVEN** the database is unreachable during shutdown
- **WHEN** the lease release fails or times out
- **THEN** shutdown proceeds
- **AND** followers acquire the lease after the TTL expires

### Requirement: Leader election defaults and configuration

`leader_election_enabled` SHALL default to true. `leader_election_ttl_seconds` SHALL default to 60 and MUST reject values below 5. Disabling leader election MUST cause the leader gate to treat every replica as leader (single-instance escape hatch), and this consequence is documented in the capability context.

The Auth Guardian scheduler is the one exception to the escape hatch: because it force-refreshes OAuth tokens and concurrent force refreshes across replicas can invalidate rotated refresh tokens, in a multi-replica deployment (instance ring larger than one) with leader election disabled the Auth Guardian scheduler MUST NOT start, and its builder MUST emit an operator-visible warning log stating that the guardian is disabled for this reason.

#### Scenario: Fresh two-replica deployment with default configuration

- **GIVEN** two replicas share one PostgreSQL database with default environment
- **WHEN** singleton schedulers tick
- **THEN** only one replica runs singleton scheduler work

#### Scenario: TTL below the minimum

- **GIVEN** `CODEX_LB_LEADER_ELECTION_TTL_SECONDS=2`
- **WHEN** settings are loaded
- **THEN** validation fails

#### Scenario: Multi-replica ring with leader election disabled

- **GIVEN** an instance ring with two replicas and `CODEX_LB_LEADER_ELECTION_ENABLED=false`
- **AND** `CODEX_LB_AUTH_GUARDIAN_ENABLED=true`
- **WHEN** the Auth Guardian scheduler is built
- **THEN** the scheduler is disabled
- **AND** a warning log states that the guardian is disabled because the ring runs without leader election
