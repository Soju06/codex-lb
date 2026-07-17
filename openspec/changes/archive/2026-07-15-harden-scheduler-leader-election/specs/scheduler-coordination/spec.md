# scheduler-coordination

## ADDED Requirements

### Requirement: Singleton schedulers gate on the shared leader lease

The usage-refresh, api-key limit reset, model refresh, sticky-session cleanup, quota planner, auth guardian, automations, data-retention, and account usage-rollup schedulers MUST execute leader-gated work only after acquiring the `scheduler_leader` lease via the leader election's `run_if_leader` gate. Because a data-retention prune or a usage-rollup fold backfill can iterate over many batches and outlive the lease TTL, they MUST NOT gate on a one-time `try_acquire`: the heartbeat-renewed `run_if_leader` gate cancels the in-flight pass on lease loss so a replica that stops being leader mid-pass stops acting on the shared tables.

Because a single shared leader-election object arbitrates every singleton scheduler on a replica, a lease acquisition failure caused by a transient database error MUST NOT demote a lease this instance already holds whose locally tracked deadline has not yet passed. One scheduler tick's failed `try_acquire` MUST NOT clear the shared leadership flag out from under another scheduler's in-progress leader-gated work. Demotion on acquisition MUST be reserved for an authoritative non-owner result (affected rowcount 0) or an acquisition failure observed after the held lease's local deadline has passed.

The authoritative non-owner verdict on acquisition MUST be evaluated identically to the renewal rowcount-0 verdict: the acquire upsert's affected-row presence MUST be captured before its commit, and a no-row result (another replica owns an unexpired lease) MUST demote the holder (clearing the leadership flag and the locally tracked deadline) and return "not acquired" even if the subsequent commit then raises on a flaky connection. A commit failure MUST NOT be re-raised and misread as a transient acquisition error — whose preservation branch would otherwise keep an already-held lease and let `run_if_leader` run a second singleton body against a row the database has already reported (no row) as owned by a different leader. Only a genuine connection error observed BEFORE that authoritative row result may take the preservation branch.

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

#### Scenario: Non-owner acquire result survives a commit failure

- **GIVEN** this instance already holds an unexpired lease whose local deadline has not passed
- **AND** a concurrent acquire's upsert RETURNS no row because another replica now owns the lease
- **AND** the subsequent commit raises on a flaky connection
- **WHEN** `try_acquire` returns
- **THEN** the holder is demoted (leadership flag and local deadline cleared) and the call returns "not acquired" rather than taking the preserved-leadership branch
- **AND** `run_if_leader` does not run another singleton body against the row owned by a different leader

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

On PostgreSQL both the stored expiry and the takeover predicate (`expires_at < now()`) MUST be computed on the database clock so that inter-replica wall-clock skew cannot steal an unexpired lease. The stored expiry (on both the acquire upsert and the renewal UPDATE) MUST be computed from the actual statement-execution clock (`clock_timestamp() + TTL`), not from the transaction-start clock (`now()` / `transaction_timestamp()`, which is fixed at transaction start). Because the `scheduler_leader` row lock serializes concurrent writers, a renewal or same-leader re-acquire that blocked on the row lock MUST therefore extend the lease from the current time, and `expires_at` MUST NOT move backward relative to a concurrent writer that committed later.

The locally tracked monotonic deadline MUST be derived from the database's OWN authoritative remaining lease, not from a Python instant chosen relative to the statement. The acquire and renewal statements MUST return, in the same statement that writes the row, the affected row's remaining lease — `expires_at` minus the database's own clock (`clock_timestamp()` on PostgreSQL; SQLite's `'now'` on SQLite), so both sides share one clock domain and the remaining is correct regardless of how long the statement waited on the row / write lock — and the caller MUST set the local deadline to a monotonic instant captured right AFTER the statement returns plus that returned remaining. This closes the clock-domain class of failures at the root: it neither BACKDATES the deadline (a monotonic instant captured BEFORE the statement would seed an already-expired deadline once a lock wait exceeds a short TTL, making a genuinely fresh lease look expired on arrival) nor lets it OUTRUN the row (a monotonic instant captured after `commit()` would overshoot the true expiry by the commit round-trip); any residual difference is bounded only by statement-return transport latency, never by the lock-wait duration. A no-match (another replica owns the lease, or a guarded renewal found it expired) MUST return no row and be read as "not acquired / lease lost". The heartbeat that renews while gated work runs MUST likewise adopt the deadline the renewal anchored from the returned remaining, rather than recomputing a pre-dispatch `now + TTL`. On SQLite (single host by construction) the same guarantee MUST be provided by evaluating the clock inside the SQL using SQLite's OWN statement-execution-time clock (e.g. `strftime('%Y-%m-%d %H:%M:%f','now')`) for the stored `acquired_at`/`expires_at`, the takeover predicate, and the renewal guard — NOT a Python timestamp bound before `session.execute`. A SQLite renewal or same-leader re-acquire can sit behind the single-writer lock (`busy_timeout`, default ~30s) for longer than the TTL (min 5s); a pre-execute Python value would then evaluate the unexpired-lease guard and the new expiry against a STALE pre-wait instant, letting a heartbeat that should have lost the lease still match and extend the row. SQLite evaluates `'now'` once per statement step, so every clock reference in one statement shares a single instant (one clock domain), and only after the write lock is held — the SQLite analog of `clock_timestamp()`. The in-SQL value MUST be produced in a format lexicographically comparable with the stored tz-aware `expires_at` column (SQLite's `'now'` is UTC, matching the naive-UTC wall clock persisted for the column; the millisecond fractional field is padded to the microsecond width the column uses).

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
- **AND** the effective lease is not shortened below the leader's locally tracked deadline, which is re-anchored to the remaining lease the renewal RETURNED

#### Scenario: Re-acquire upsert blocks on the lease row lock

- **GIVEN** a PostgreSQL replica whose acquire upsert takes the `ON CONFLICT DO UPDATE` path
- **AND** the upsert blocks on the `scheduler_leader` row lock for a duration approaching the TTL
- **WHEN** the conflict update commits
- **THEN** the stored `expires_at` is recomputed from `clock_timestamp()` in the current statement rather than the `VALUES`/`excluded` tuple captured before the wait
- **AND** the local deadline `try_acquire` records is anchored to the remaining lease the statement RETURNED from a monotonic instant captured AFTER it executed, so it reflects the full remaining TTL (not a backdated, already-expired deadline)

#### Scenario: SQLite renewal executes after the lease has expired

- **GIVEN** a SQLite deployment holding a lease whose `expires_at` is imminent
- **AND** the renewal UPDATE sits behind the single-writer lock until after `expires_at` has passed
- **WHEN** the write lock is finally acquired and the statement executes
- **THEN** the unexpired-lease guard and the new expiry are evaluated on SQLite's execution-time clock (`strftime(...,'now')` in the SQL), not a Python timestamp captured before the wait
- **AND** the guarded UPDATE matches 0 rows, so the holder demotes and the expired row is left untouched (not extended) for a follower to take

#### Scenario: Lock-delayed acquire yields a full-remaining local deadline

- **GIVEN** a deployment where the acquire (or renewal) statement waits on the row / write lock for longer than a short TTL before it executes
- **WHEN** the statement finally executes and RETURNS the row's remaining lease measured on the database's own clock
- **THEN** the local monotonic deadline is set to an instant captured after the statement returns plus that returned remaining, so it reflects ~the full remaining TTL rather than a backdated, already-expired deadline
- **AND** the local deadline does not outrun the database `expires_at` beyond statement-return transport latency

### Requirement: Leaders renew the lease while gated work runs and demote on loss

While leader-gated work executes, the lease holder MUST renew the lease at an interval no greater than one third of the TTL. Each heartbeat sleep before a renewal MUST be capped so the renewal it schedules BEGINS with enough budget to COMPLETE before the holder's locally tracked lease deadline: the sleep MUST NOT exceed the time remaining until that deadline minus the renewal time-box (clamped to at least zero, and never exceeding the renew interval), so that the sum of the sleep and the renewal time-box lands at or before the deadline. When the holder is already within one renewal time-box of the deadline the sleep MUST be zero and the renewal MUST fire immediately; when the deadline has already passed the holder MUST demote immediately without sleeping or renewing. Bounding only the sleep by the remaining time is INSUFFICIENT: a preserved acquire — which does not extend the database `expires_at` and can leave less than one renew interval, or even less than one renewal time-box, remaining — combined with a stalled renewal DB call / pool checkout would otherwise start `renew()` at the deadline and let its full time-box run AFTER the database lease has already expired, keeping the gated body acting as leader for up to the time-box past expiry while a follower is free to acquire the row. Each renewal attempt MUST be time-boxed to no more than one sixth of the TTL so that a hung database call cannot silently extend leadership; a timed-out attempt counts as a renewal error. The renewal wait MUST additionally be bounded so it never extends past the locally tracked lease deadline: a renewal still in flight when the deadline is reached MUST be abandoned and the holder demoted, so the gated body is cancelled the instant the deadline is reached with no successful renewal rather than being left to run as leader for the remainder of the time-box. The time-box MUST be enforced against the elapsed timeout alone: once the timeout elapses the attempt MUST be counted as an error immediately and the heartbeat MUST NOT block on the renewal coroutine's cancellation or cleanup unwinding (e.g. a blocked rollback during session teardown), which could otherwise defer demotion past the lease deadline. The renewal UPDATE MUST be conditional on the lease still being unexpired: it MUST match only when `expires_at` is still in the future, evaluated in the lease's clock domain (`clock_timestamp()` on PostgreSQL, SQLite's own statement-execution-time clock — e.g. `strftime(...,'now')` in the SQL, not a pre-execute Python value — on SQLite). Keying the renewal solely on `id` + `leader_id` is insufficient, because a heartbeat delayed past the lease deadline (event-loop stall, row-lock wait) could otherwise extend a row whose `expires_at` has already passed while no follower has yet claimed it — resurrecting a dead lease and keeping leader-gated work running past the TTL, which delays or overlaps failover. When the lease has already expired the renewal MUST match 0 rows, which is treated identically to the takeover rowcount-0 case below (the holder demotes and a follower is free to acquire the row). The release/drain renewal path MUST likewise be guarded so it can never resurrect an expired lease. Renewal MUST verify that the renewal UPDATE affected a row; an affected rowcount of 0 MUST demote the holder and request cancellation of the in-flight gated work. This rowcount-0 verdict is authoritative: the affected rowcount MUST be captured before the renewal's commit, and the demotion (clearing the leadership flag and the locally tracked deadline) MUST be applied even if the subsequent commit then fails. A commit failure on a flaky connection during a takeover MUST NOT be re-raised and misread by the heartbeat as a transient renewal error — which would keep the gated body running as a believed-leader until another error or the local deadline — when the database has already reported (rowcount 0) that the caller is no longer the owner. Two consecutive renewal errors MUST demote the holder likewise, and any renewal error observed after the holder's locally tracked lease deadline (last successful renewal or acquisition plus TTL) has passed MUST demote immediately, so a leader with a hung or unreachable database demotes itself no later than the lease TTL. However, because a single shared leader-election object arbitrates every singleton scheduler, multiple `run_if_leader` heartbeats can renew the SAME lease concurrently, each tracking its own local deadline while sharing the leadership flag and shared lease deadline. A heartbeat's local transient-error demotion (two consecutive errors, or an error at/after ITS local deadline) MUST NOT clear the shared leadership flag or shared deadline when a concurrent heartbeat has advanced the shared lease deadline beyond this heartbeat's local deadline — i.e. another heartbeat renewed the row more recently. Clearing the shared state on one heartbeat's local errors would cancel a sibling's otherwise-valid leader work and make the sibling's next renewal return "not leader" without touching the database, even though the database lease is freshly held by the same process. In that case the heartbeat MUST adopt the sibling's fresher shared deadline and keep renewing rather than tearing down shared leadership; the shared flag MUST be cleared only on an authoritative rowcount-0 loss (which clears it immediately, per above) or a genuinely expired shared lease deadline. A heartbeat that loses confidence in its own renewals MAY stop trusting them, but MUST NOT cancel a sibling's valid leadership.

After demotion the gate MUST await the cancelled body for at most a bounded grace period and then detach it, so the gate itself stops within one renew interval plus the grace. A body that shields in-flight singleton refresh work (token or usage refresh singleflights) MAY drain that work concurrently with a new leader; this residual overlap is bounded by the underlying operation's own timeout and is documented with its safety argument in the capability context.

When the gate is instead cancelled externally (graceful shutdown) while the lease is still held — as opposed to the lease-loss branch, where the lease is already gone and MUST NOT be renewed — the gate MUST keep renewing the lease while it cancels and drains the gated body, and MUST stop the heartbeat only after the body has exited (bounded by the same cancel grace). This prevents a body that honours cancellation slower than the remaining lease TTL (e.g. a shielded token/usage refresh with a short TTL) from letting the database lease expire while it still runs as leader, which would let a follower acquire the lease and run the same singleton work concurrently.

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

#### Scenario: Stalled renewal near the deadline demotes at the deadline, not a time-box later

- **GIVEN** a leader whose held lease was seeded from a preserved acquire and has less than one renewal time-box (`ttl / 6`) remaining on its locally tracked deadline
- **AND** the next renewal's DB call / pool checkout stalls
- **WHEN** the heartbeat schedules the renewal
- **THEN** the renewal begins immediately (the capped sleep is zero) rather than after a full sleep to the deadline
- **AND** the renewal wait is bounded so it does not extend past the deadline; the still-in-flight renewal is abandoned at the deadline
- **AND** the holder demotes and cancels the gated body at the deadline, so the body never acts as leader past the database lease expiry

#### Scenario: Delayed renewal does not resurrect an expired lease

- **GIVEN** a leader whose renewal is delayed past the lease `expires_at` (e.g. an event-loop stall or row-lock wait) while no follower has yet claimed the row
- **WHEN** the delayed renewal UPDATE finally executes
- **THEN** the UPDATE matches 0 rows because it is guarded on the lease still being unexpired, so it does NOT extend the already-expired row
- **AND** the holder demotes (leadership flag and local deadline cleared) and cancels the in-flight gated work, leaving the expired row free for a follower to acquire

#### Scenario: Lease loss observed by rowcount survives a commit failure

- **GIVEN** a leader whose renewal UPDATE affects rowcount 0 because another replica took over the lease
- **AND** the subsequent commit raises on a flaky connection
- **WHEN** the renewal returns to the heartbeat
- **THEN** the holder is demoted (leadership flag and local deadline cleared) rather than treated as a transient renewal error
- **AND** the in-flight gated task is cancelled immediately instead of running until another error or the local deadline

#### Scenario: Graceful shutdown drains the body while renewing the lease

- **GIVEN** a leader whose gated body is draining a shielded refresh that honours cancellation slower than the remaining lease TTL
- **WHEN** the gate is cancelled externally by graceful shutdown while the lease is still held
- **THEN** the heartbeat keeps renewing the lease until the body has exited (bounded by the cancel grace)
- **AND** the database lease does not expire while the old body still runs, so no follower can acquire it and run the same singleton work concurrently

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

#### Scenario: Concurrent heartbeat's renewal preserves shared leadership

- **GIVEN** two singleton scheduler bodies running concurrently on the SAME shared leader election, each with its own `run_if_leader` heartbeat renewing the one `scheduler_leader` row
- **AND** heartbeat A renews successfully, advancing the shared lease deadline
- **WHEN** heartbeat B hits the consecutive transient-renewal-error limit (or an error at/after B's own stale local deadline) while the shared deadline A advanced is still in the future
- **THEN** B does NOT clear the shared leadership flag or shared deadline
- **AND** B adopts A's fresher shared deadline and keeps renewing rather than demoting
- **AND** A's gated work is not cancelled and A's next renewal still succeeds
- **AND** the shared leadership flag would be cleared only on an authoritative rowcount-0 loss or a genuinely expired shared deadline

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

On lifespan shutdown, after all schedulers are stopped, the process MUST delete the `scheduler_leader` row it holds (matching its own leader id). Before deleting the row, release MUST wait a bounded grace for any gated body that was detached still draining shielded singleton work. A body detached on the graceful-shutdown path is still the rightful leader, so while release waits for it the lease MUST NOT be left to expire under it: release MUST keep renewing the lease at an interval no greater than one third of the TTL for as long as it waits, so that with the minimum TTL (5s) the database lease cannot expire during the drain wait and a follower cannot acquire it and run the same singleton work concurrently with the still-draining body. If such a body is still running after the grace, release MUST skip deleting the row entirely — the lease then expires after its TTL (roughly one further TTL past the last renewal, after which the detached body is treated as abandoned) — so a follower cannot acquire the lease while the shutting-down process may still execute leader-gated work. Release failure MUST NOT block or fail shutdown; the lease then simply expires after the TTL. On a shared single-writer SQLite deployment the best-effort lease writes (the shutdown drain/keeper renewals and the release DELETE) contend with the process's other database work for the write lock; a transient `database is locked` (or `busy`) on these best-effort paths MUST NOT be surfaced as a lease-release failure — it MUST be swallowed and logged at debug, and the renewal MUST be retried on its next cadence (the release simply leaves the row to expire after the TTL) — so SQLite write contention during shutdown neither propagates out of the shutdown path nor spams warnings. Non-lock errors keep their warning so genuine faults stay visible.

Because the schedulers are stopped one at a time and only the final scheduler's teardown triggers release, an earlier scheduler's `stop()` can detach a shielded leader-gated body — which cancels that scheduler's own lease heartbeat — while later schedulers are still stopping and release has not begun its drain-renewal. To close that cross-scheduler stop-sequence gap, from the moment shutdown begins (BEFORE the first scheduler is stopped) until release takes over renewal, a SINGLE process-level renewal owner MUST renew the lease continuously at an interval no greater than one third of the TTL. This guarantees that the database lease is renewed by exactly one owner from shutdown-begin through the end of the drain, so with the minimum TTL (5s) the lease can never expire while any detached or draining leader-gated body across ALL schedulers could still be acting as leader, even when a later scheduler takes at least the TTL to drain or detach. Renewal ownership MUST pass from this shutdown renewer to release's own bounded drain-renewal without an overlapping-writer race and without a gap. This continuous-renewal obligation is itself bounded: it renews only while release has not yet abandoned the row, so if a body outlives the release drain grace the renewer is stopped and the lease is left to expire after its TTL once the body is treated as abandoned — the whole shutdown release path MUST still complete within its overall deadline even if a body wedges.

The shutdown release step MUST be bounded by a deadline that holds even when the database is wedged. Because the release path opens a background session whose rollback/close shield and await their own teardown, cancelling an awaited release (e.g. via `asyncio.wait_for`) would not unwind a stuck database call and could still pin shutdown past the deadline. The release therefore MUST be run as a separate task and abandoned — not awaited — once the deadline elapses, so shutdown always proceeds within the deadline; the abandoned release's eventual outcome MAY be logged and the lease then expires after its TTL.

#### Scenario: Leader shuts down cleanly

- **GIVEN** a two-replica deployment where the leader begins graceful shutdown
- **WHEN** the leader's lifespan teardown completes
- **THEN** the lease row is deleted
- **AND** the surviving replica acquires the lease on its next tick without waiting for TTL expiry

#### Scenario: Shutdown renews the lease while a detached body drains

- **GIVEN** a leader shutting down with the minimum TTL while a detached gated body is still draining shielded refresh work as the rightful leader
- **WHEN** release waits for the body across more than one renew interval
- **THEN** release keeps renewing the lease on the heartbeat cadence so the database lease does not expire under the still-draining body
- **AND** no follower can acquire the lease while the body may still act as leader
- **AND** once the body drains the lease row is deleted

#### Scenario: Renewal is continuous across the cross-scheduler stop sequence

- **GIVEN** a leader shutting down with the minimum TTL whose schedulers are stopped one at a time
- **AND** an earlier scheduler's `stop()` detaches a shielded gated body, cancelling that scheduler's own lease heartbeat
- **WHEN** the remaining schedulers take at least the TTL to stop before release begins
- **THEN** a single process-level renewal owner started at shutdown-begin keeps renewing the lease on the heartbeat cadence throughout that whole window
- **AND** the database lease never expires while the detached body may still act as leader, so no follower can acquire it and run the same singleton work concurrently
- **AND** renewal ownership passes to release's own drain-renewal with no overlapping writer and no gap

#### Scenario: Shutdown with a detached gated body still draining

- **GIVEN** a leader shutting down while a detached gated body is still draining shielded refresh work
- **WHEN** the release drain grace elapses with the body still running
- **THEN** the lease row is not deleted
- **AND** shutdown proceeds and followers acquire the lease only after the TTL expires (roughly one further TTL past the last renewal, after which the body is treated as abandoned)

#### Scenario: Release fails during shutdown

- **GIVEN** the database is unreachable during shutdown
- **WHEN** the lease release fails or times out
- **THEN** shutdown proceeds
- **AND** followers acquire the lease after the TTL expires

#### Scenario: Transient SQLite lock on a best-effort lease write is tolerated

- **GIVEN** a shared single-writer SQLite deployment where the shutdown drain/keeper renewal or the release DELETE loses the write-lock race and raises `database is locked`
- **WHEN** that best-effort lease write fails
- **THEN** the error is swallowed and logged at debug rather than surfaced as a lease-release failure
- **AND** shutdown proceeds; the renewal is retried on its next cadence and the release leaves the row to expire after the TTL
- **AND** a non-lock error on the same path still surfaces as a warning

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
