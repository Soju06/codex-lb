# scheduler-coordination

## ADDED Requirements

### Requirement: Singleton schedulers gate on the shared leader lease

The usage-refresh, api-key limit reset, model refresh, sticky-session cleanup, quota planner, auth guardian, and automations schedulers MUST execute leader-gated work only after acquiring the `scheduler_leader` lease via the leader election's `run_if_leader` gate.

Because a single shared leader-election object arbitrates every singleton scheduler on a replica, a lease acquisition failure caused by a transient database error MUST NOT demote a lease this instance already holds whose locally tracked deadline has not yet passed. One scheduler tick's failed `try_acquire` MUST NOT clear the shared leadership flag out from under another scheduler's in-progress leader-gated work. Demotion on acquisition MUST be reserved for an authoritative non-owner result (affected rowcount 0) or an acquisition failure observed after the held lease's local deadline has passed.

A preserved-leadership outcome (a transient acquire error that keeps an already-held lease) MUST NOT be presented to callers as a fresh acquisition: because the failed attempt did not extend the database `expires_at`, it MUST NOT extend or reset the locally tracked lease deadline. The locally tracked deadline MUST be advanced only by an acquire or renewal that actually wrote the database row (affected rowcount 1); `run_if_leader` and its heartbeat MUST seed and extend their working deadline from that DB-confirmed value, never from a full TTL granted on a preserved acquire. Consequently the local deadline can never exceed the last DB-confirmed expiry, so a leader whose renewals keep failing after a preserved acquire demotes itself no later than that expiry and a follower can take over the row once the true lease expires.

#### Scenario: Two replicas tick concurrently

- **GIVEN** two replicas share one database
- **WHEN** the same singleton scheduler ticks concurrently on both replicas
- **THEN** exactly one replica executes the tick body
- **AND** the other replica skips the tick without side effects

#### Scenario: Lease acquisition errors

- **GIVEN** the database is unreachable during lease acquisition
- **AND** this replica does not already hold a valid lease
- **WHEN** a scheduler ticks
- **THEN** the replica treats itself as non-leader and skips the tick
- **AND** the scheduler retries acquisition on its next tick

#### Scenario: Concurrent acquire error while a valid lease is held

- **GIVEN** this instance already holds an unexpired lease and is running leader-gated work
- **WHEN** another singleton scheduler's `try_acquire` on the shared leader election hits a transient database error before the held lease's local deadline passes
- **THEN** leadership is preserved and the in-progress gated work is not cancelled
- **AND** once the local deadline has passed a transient acquire error demotes the holder

#### Scenario: Preserved acquire does not extend the local heartbeat deadline

- **GIVEN** a leader running `run_if_leader` whose held lease has a DB-confirmed deadline that is still valid but close to expiry
- **AND** the database becomes unreachable so `run_if_leader`'s entry `try_acquire` preserves leadership without writing the lease row
- **WHEN** the gate's heartbeat seeds its working deadline
- **THEN** it uses the last DB-confirmed expiry rather than a fresh full TTL
- **AND** with renewals continuing to fail the holder demotes no later than that DB-confirmed expiry, so a follower can acquire the row once the true lease expires

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

On PostgreSQL both the stored expiry and the takeover predicate (`expires_at < now()`) MUST be computed on the database clock so that inter-replica wall-clock skew cannot steal an unexpired lease. The stored expiry (on both the acquire upsert and the renewal UPDATE) MUST be computed from the actual statement-execution clock (`clock_timestamp() + TTL`), not from the transaction-start clock (`now()` / `transaction_timestamp()`, which is fixed at transaction start). Because the `scheduler_leader` row lock serializes concurrent writers, a renewal or same-leader re-acquire that blocked on the row lock MUST therefore extend the lease from the current time, and `expires_at` MUST NOT move backward relative to a concurrent writer that committed later — so a leader's locally tracked deadline can never outrun the database expiry. On SQLite (single host by construction) the host clock MAY be used, bound consistently on both sides of the comparison by the same writer.

#### Scenario: Acquiring replica's wall clock is ahead

- **GIVEN** a PostgreSQL deployment where a follower's wall clock is 45 seconds ahead of the leader's
- **AND** the leader holds an unexpired lease
- **WHEN** the follower calls `try_acquire`
- **THEN** the lease is not stolen because expiry is evaluated against the database clock

#### Scenario: Overlapping renewals block on the lease row lock

- **GIVEN** two leader-gated schedulers renewing the same lease on one PostgreSQL replica
- **AND** their renewal UPDATEs queue on the `scheduler_leader` row lock
- **WHEN** an earlier-started renewal commits after a later-started one
- **THEN** the stored `expires_at` reflects each renewal's statement-execution time and never moves backward
- **AND** the effective lease is not shortened below the leader's locally tracked deadline

#### Scenario: Re-acquire upsert blocks on the lease row lock

- **GIVEN** a PostgreSQL replica whose acquire upsert takes the `ON CONFLICT DO UPDATE` path
- **AND** the upsert blocks on the `scheduler_leader` row lock for a duration approaching the TTL
- **WHEN** the conflict update commits
- **THEN** the stored `expires_at` is recomputed from `clock_timestamp()` in the current statement rather than the `VALUES`/`excluded` tuple captured before the wait
- **AND** the committed lease is not already stale relative to the fresh local deadline `try_acquire` records after commit

### Requirement: Leaders renew the lease while gated work runs and demote on loss

While leader-gated work executes, the lease holder MUST renew the lease at an interval no greater than one third of the TTL. Each heartbeat sleep before a renewal MUST additionally be bounded by the time remaining until the holder's locally tracked lease deadline (i.e. it MUST NOT exceed that remaining time, even when that is shorter than the renew interval); when that deadline has already passed the holder MUST demote immediately without sleeping. This prevents a lease seeded from a preserved acquire — which does not extend the database `expires_at` and can leave less than a renew interval remaining — from keeping the gated body running past the true database expiry. Each renewal attempt MUST be time-boxed to no more than one sixth of the TTL so that a hung database call cannot silently extend leadership; a timed-out attempt counts as a renewal error. The time-box MUST be enforced against the elapsed timeout alone: once the timeout elapses the attempt MUST be counted as an error immediately and the heartbeat MUST NOT block on the renewal coroutine's cancellation or cleanup unwinding (e.g. a blocked rollback during session teardown), which could otherwise defer demotion past the lease deadline. Renewal MUST verify that the renewal UPDATE affected a row; an affected rowcount of 0 MUST demote the holder and request cancellation of the in-flight gated work. Two consecutive renewal errors MUST demote the holder likewise, and any renewal error observed after the holder's locally tracked lease deadline (last successful renewal or acquisition plus TTL) has passed MUST demote immediately, so a leader with a hung or unreachable database demotes itself no later than the lease TTL.

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

#### Scenario: Renewal cancellation cleanup hangs

- **GIVEN** a leader whose renewal database calls stall and whose cancellation cleanup does not unwind promptly
- **WHEN** each time-boxed renewal attempt's timeout elapses while the renewal coroutine is still unwinding
- **THEN** the attempt is counted as an error on the timeout without awaiting the renewal's cancellation
- **AND** the leader demotes itself and cancels the in-flight gated task no later than the lease TTL

#### Scenario: Heartbeat sleep bounded by a near preserved deadline

- **GIVEN** a leader whose heartbeat is seeded from a preserved-acquire deadline with less than one renew interval remaining
- **WHEN** the heartbeat schedules its next renewal
- **THEN** it sleeps no longer than the time remaining until that deadline rather than a full renew interval
- **AND** if the seeded deadline has already passed it demotes immediately without sleeping or attempting a renewal
- **AND** the gated body cannot keep running a full renew interval past the true database expiry

#### Scenario: Body shields in-flight refresh work past cancellation

- **GIVEN** a leader whose gated body is inside a shielded singleton refresh when the lease is lost
- **WHEN** the gate cancels the body and the body keeps draining the shielded work
- **THEN** the gate stops awaiting after the bounded grace period and returns as non-leader
- **AND** the detached body is bounded by the refresh operation's own timeout and its outcome is logged

### Requirement: Lease is released on graceful shutdown

On lifespan shutdown, after all schedulers are stopped, the process MUST delete the `scheduler_leader` row it holds (matching its own leader id). Before deleting the row, release MUST wait a bounded grace for any gated body that was detached still draining shielded singleton work; if such a body is still running after the grace, release MUST skip deleting the row entirely — the lease then expires after its TTL — so a follower cannot acquire the lease while the shutting-down process may still execute leader-gated work. Release failure MUST NOT block or fail shutdown; the lease then simply expires after the TTL.

The shutdown release step MUST be bounded by a deadline that holds even when the database is wedged. Because the release path opens a background session whose rollback/close shield and await their own teardown, cancelling an awaited release (e.g. via `asyncio.wait_for`) would not unwind a stuck database call and could still pin shutdown past the deadline. The release therefore MUST be run as a separate task and abandoned — not awaited — once the deadline elapses, so shutdown always proceeds within the deadline; the abandoned release's eventual outcome MAY be logged and the lease then expires after its TTL.

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

#### Scenario: Release stalls on a wedged database

- **GIVEN** a leader shutting down while the lease-release database call is wedged and its cancellation cannot unwind promptly
- **WHEN** the shutdown release deadline elapses
- **THEN** shutdown abandons the release task and proceeds within the deadline
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
