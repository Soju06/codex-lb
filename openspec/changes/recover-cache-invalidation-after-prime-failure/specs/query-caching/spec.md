## MODIFIED Requirements

### Requirement: Cache invalidation bumps and polling are resilient and observable

`bump()` MUST retry transient write failures (including SQLite "database is locked") with a short backoff; on final failure it MUST log at ERROR with the namespace, increment `codex_lb_cache_invalidation_bump_failures_total{namespace}`, and MUST NOT fail the originating mutation. Coalesced (`request_bump`) namespaces MUST remain pending and be retried on subsequent poll cycles until a bump succeeds, and a `request_bump` arriving while a flush for the same namespace is already awaiting its bump MUST be preserved and produce a later bump. When any invalidation callback for a namespace fails, the poller MUST NOT acknowledge the observed version and MUST re-run that namespace's callbacks on subsequent poll cycles until they succeed. The poller MUST escalate consecutive poll failures above debug level after a bounded count (WARNING after 3, ERROR after 10) and increment `codex_lb_cache_invalidation_poll_failures_total`. After a startup baseline read fails, a process that continues without a recorded baseline MUST treat each positive version first observed for a registered namespace by the next successful background poll as changed, run that namespace's registered callbacks, and acknowledge the version only after those callbacks succeed. This recovery MAY cause a redundant invalidation for a version that predates startup; it MUST NOT silently absorb a peer bump into a callback-less baseline.

#### Scenario: Bump failure under database lock is observable and does not fail the mutation

- **GIVEN** the database rejects cache-invalidation writes with a lock error for longer than the retry budget
- **WHEN** a mutation attempts a durable namespace bump
- **THEN** the mutation itself still succeeds
- **AND** an ERROR log naming the namespace is emitted and the bump-failure counter increments

#### Scenario: Pending coalesced namespace flushes on the next successful cycle

- **GIVEN** a coalesced `request_bump` namespace failed to flush during a poll cycle
- **WHEN** the database becomes writable again
- **THEN** the next poll cycle flushes the pending namespace and increments its version

#### Scenario: Bump requested during an in-flight flush produces a later bump

- **GIVEN** a coalesced flush is awaiting the bump write for a namespace
- **WHEN** another mutation commits and requests a bump for the same namespace before the flush completes
- **THEN** the namespace is re-queued and flushed again on a subsequent cycle, incrementing the version beyond the in-flight bump

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
