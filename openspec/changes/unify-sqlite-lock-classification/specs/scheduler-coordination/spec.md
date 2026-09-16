## MODIFIED Requirements

### Requirement: Lease is released on graceful shutdown

On lifespan shutdown, after all schedulers are stopped, the process MUST delete the `scheduler_leader` row it holds (matching its own leader id). Before deleting the row, release MUST wait a bounded grace for any gated body that was detached still draining shielded singleton work. A body detached on the graceful-shutdown path is still the rightful leader, so while release waits for it the lease MUST NOT be left to expire under it: release MUST keep renewing the lease at an interval no greater than one third of the TTL for as long as it waits, so that with the minimum TTL (5s) the database lease cannot expire during the drain wait and a follower cannot acquire it and run the same singleton work concurrently with the still-draining body. If such a body is still running after the grace, release MUST skip deleting the row entirely — the lease then expires after its TTL (roughly one further TTL past the last renewal, after which the detached body is treated as abandoned) — so a follower cannot acquire the lease while the shutting-down process may still execute leader-gated work. Release failure MUST NOT block or fail shutdown; the lease then simply expires after the TTL. On a shared single-writer SQLite deployment the best-effort lease writes (the shutdown drain/keeper renewals and the release DELETE) contend with the process's other database work for the write lock; a transient SQLite write-lock failure on these best-effort paths MUST NOT be surfaced as a lease-release failure — it MUST be swallowed and logged at debug, and the renewal MUST be retried on its next cadence (the release simply leaves the row to expire after the TTL) — so SQLite write contention during shutdown neither propagates out of the shutdown path nor spams warnings. Neither path may retry the write in place: the renewal's next cadence IS its retry and the release is one-shot, so both MUST keep failing fast rather than holding shutdown past its deadline. What counts as a transient write-lock failure MUST be the process-wide shared classification (SQLITE_BUSY's `database is locked` / `database is busy`, SQLITE_LOCKED's `database table is locked` / `database schema is locked`, and a `busy_snapshot` result-code name in the message) rather than a predicate private to leader election, and the debug report MUST name the driver's extended result code (`sqlite_errorname`, or its absence) so an instant SQLITE_BUSY_SNAPSHOT can be told from a SQLITE_BUSY returned only after the full busy timeout. Non-lock errors keep their warning so genuine faults stay visible.

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
- **AND** the debug report names the driver's `sqlite_errorname`, or records its absence

#### Scenario: Release stalls on a wedged database

- **GIVEN** a leader shutting down while the lease-release database call is wedged and its cancellation cannot unwind promptly
- **WHEN** the shutdown release deadline elapses
- **THEN** shutdown abandons the release task and proceeds within the deadline
- **AND** followers acquire the lease after the TTL expires
