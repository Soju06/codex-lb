## MODIFIED Requirements

### Requirement: Cache invalidation bumps and polling are resilient and observable

`bump()` MUST retry transient write failures (including SQLite "database is locked") with a short backoff; on final failure it MUST log at ERROR with the namespace, increment `codex_lb_cache_invalidation_bump_failures_total{namespace}`, MUST NOT fail the originating mutation, and MUST leave the namespace pending for subsequent poll cycles until a bump succeeds. Direct awaited and coalesced (`request_bump`) publications MUST share this retained retry guarantee while the originating process continues polling. Pending work MUST be coalesced to one marker per namespace. A write MUST consume an older pending marker before attempting publication; a marker requested while an immediate or coalesced write is in flight MUST be preserved and produce a later bump. An aborted write MUST restore pending work regardless of whether the database had already accepted its commit. Cancellation during an awaited write or its retry backoff MUST propagate to the caller without dropping the pending publication. A write that raises MUST NOT prevent the remaining pending namespaces from flushing in the same cycle. When any invalidation callback for a namespace fails, the poller MUST NOT acknowledge the observed version and MUST re-run that namespace's callbacks on subsequent poll cycles until they succeed. The poller MUST escalate consecutive poll failures above debug level after a bounded count (WARNING after 3, ERROR after 10) and increment `codex_lb_cache_invalidation_poll_failures_total`. After a startup baseline read fails, a process that continues without a recorded baseline MUST treat each positive version first observed for a registered namespace by the next successful background poll as changed, run that namespace's registered callbacks, and acknowledge the version only after those callbacks succeed. This recovery MAY cause a redundant invalidation for a version that predates startup; it MUST NOT silently absorb a peer bump into a callback-less baseline.

#### Scenario: Bump failure under database lock is observable and does not fail the mutation
- **GIVEN** the database rejects cache-invalidation writes with a lock error for longer than the retry budget
- **WHEN** a mutation attempts a durable namespace bump
- **THEN** the mutation itself still succeeds
- **AND** an ERROR log naming the namespace is emitted and the bump-failure counter increments
- **AND** the namespace remains pending for a later poll cycle

#### Scenario: Pending namespace flushes on the next successful cycle
- **GIVEN** an awaited or coalesced namespace bump failed during a mutation or poll cycle
- **WHEN** the database becomes writable again
- **THEN** the next poll cycle flushes the pending namespace and increments its version

#### Scenario: Bump requested during an in-flight write produces a later bump
- **GIVEN** an immediate or coalesced write is awaiting the bump for a namespace
- **WHEN** another mutation commits and requests a bump for the same namespace before the write completes
- **THEN** the namespace stays queued and is flushed on a subsequent cycle, incrementing the version beyond the in-flight bump

#### Scenario: Successful immediate publication consumes earlier queued work
- **GIVEN** a namespace has a pending marker before an immediate publication starts
- **AND** no further mutation requests that namespace during the write
- **WHEN** the immediate publication succeeds
- **THEN** a subsequent poll cycle does not write an extra bump for the already-covered marker

#### Scenario: Failed invalidation callback keeps the version unacknowledged and is retried
- **GIVEN** a replica observes an `account_routing` version bump
- **AND** its routing snapshot refresh fails with a transient database error
- **WHEN** the poll cycle completes
- **THEN** the replica does not record the new version as seen
- **AND** the refresh is retried on subsequent poll cycles until it succeeds

#### Scenario: Consecutive poll failures escalate above debug
- **GIVEN** a replica's poller cannot read the `cache_invalidation` table
- **WHEN** three consecutive polls fail
- **THEN** a WARNING is logged and the poll-failure counter increments

#### Scenario: Failed startup prime cannot absorb a route-cache bump
- **GIVEN** replica B's startup cache-invalidation baseline read fails and no `upstream_route` version is recorded
- **AND** replica B continues serving traffic and warms an upstream-route resolution cache entry
- **WHEN** replica A commits a route-input mutation and advances `upstream_route` before replica B's first successful version read
- **THEN** replica B's first successful background poll MUST run the registered `upstream_route` invalidation callback before acknowledging the observed version
- **AND** the warmed route entry MUST be cleared in that poll instead of remaining stale until its TTL or a later bump

#### Scenario: An aborted bump write keeps its namespace queued

- **GIVEN** an awaited or coalesced publication has consumed an earlier pending marker and is awaiting its write
- **WHEN** that write aborts — cancelled or raised — before the database accepts its commit
- **THEN** the namespace is restored to the pending set for a later cycle, and no version is written

#### Scenario: A raising namespace does not starve the others

- **GIVEN** two pending namespaces where the first (in sort order) raises on every bump attempt
- **WHEN** a flush cycle runs
- **THEN** the raising namespace stays pending with no version written
- **AND** the other namespace is bumped in that same cycle

#### Scenario: An abort after the commit was accepted still restores the namespace

- **GIVEN** a bump write aborts — cancelled, or the driver raises — after the database accepted its commit but before completion is reported
- **WHEN** the abort is handled
- **THEN** the namespace is restored to the pending set and bumped on a later cycle
- **AND** the resulting duplicate version increment is accepted

#### Scenario: Cancellation during immediate retry backoff retains publication
- **GIVEN** an immediate invalidation write encounters a transient lock error and is waiting for its next retry
- **WHEN** the originating task is cancelled
- **THEN** cancellation propagates to that task
- **AND** the namespace remains pending for a later poll cycle

#### Scenario: API-key update converges after failed immediate notification
- **GIVEN** replica B holds cached active auth data for an API key
- **WHEN** replica A commits an API update disabling the key but its immediate notification exhausts the retry budget
- **THEN** the API update succeeds with the key disabled and replica A clears its local auth cache
- **AND** the next successful source flush and peer poll clear replica B's cached active auth data without waiting for its TTL

#### Scenario: Account pause converges after failed immediate notification
- **GIVEN** replica B holds a routing snapshot in which an account is active
- **WHEN** replica A commits an account pause but its immediate notification exhausts the retry budget
- **THEN** the pause API succeeds
- **AND** the next successful source flush and peer poll mark the account unavailable on replica B without requiring another mutation
