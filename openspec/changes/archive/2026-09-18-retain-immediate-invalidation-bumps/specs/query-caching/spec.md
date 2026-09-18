## MODIFIED Requirements

### Requirement: Cross-replica cache invalidation bus bounds process-local cache staleness
Every process-local cache that serves security, authorization, or routing decisions MUST either register a namespace on the cross-replica cache-invalidation bus or declare a documented maximum cross-replica staleness TTL. Mutations MUST commit durable state before (or independently of) bumping their namespace version, each process MUST poll the `cache_invalidation` version table at a bounded interval (default 0.5s) and run registered namespace callbacks on version change, and the registered namespaces MUST include `api_key`, `firewall`, `account_routing`, `account_selection`, `settings`, and `upstream_route`. Each process MUST seed its baseline namespace versions before loading local caches / routing snapshots and before serving traffic, so a peer bump committed after that baseline is observed as a change (runs callbacks) rather than acknowledged as pre-existing state. While the originating poller remains running, a failed or interrupted invalidation MUST stay pending until a later cycle writes it successfully. A cache's TTL remains the fallback staleness bound for out-of-band database edits, process exit before a pending bump drains, or a poller that remains unavailable.

#### Scenario: Selection-state change on one replica converges on peers within the bus bound

- **GIVEN** two replicas share one database and each runs the cache-invalidation poller
- **AND** replica B holds warm cached selection inputs that include account X
- **WHEN** replica A persists a state change for account X and invalidates its selection cache with propagation
- **THEN** the `account_selection` namespace version is bumped within one poll cycle
- **AND** replica B's selection cache is invalidated on its next poll, without waiting for the cache TTL

#### Scenario: Peer bump committed before the first poll is not lost

- **GIVEN** a starting replica seeds its baseline namespace versions before loading its routing snapshot and before serving traffic
- **WHEN** a peer commits a mutation and bumps `account_routing` (or `settings`, or `account_selection`) after this replica loaded its caches but before its first poll cycle
- **THEN** the replica's first poll observes the bumped version as a change and runs the namespace callbacks
- **AND** the peer bump is not silently acknowledged as a baseline, so the cache is not left stale until the fallback TTL

#### Scenario: New security-relevant cache without bus coverage is a spec violation

- **GIVEN** a contributor adds a new process-local cache that gates a security, authorization, or routing decision
- **WHEN** the cache neither registers a cache-invalidation namespace nor documents a maximum cross-replica staleness TTL
- **THEN** the change violates this capability and is rejected at review

#### Scenario: Process loss still converges within the fallback TTL

- **GIVEN** a mutation commits but its process exits before the namespace bump or queued retry lands
- **WHEN** peer replicas keep serving their cached values
- **THEN** each peer converges no later than that cache's documented fallback TTL

### Requirement: Cache invalidation bumps and polling are resilient and observable

`bump()` MUST retry transient write failures (including SQLite "database is locked") with a short backoff; on final failure it MUST log at ERROR with the namespace, increment `codex_lb_cache_invalidation_bump_failures_total{namespace}`, MUST NOT fail the originating mutation, and MUST leave the namespace pending for subsequent poll cycles until a bump succeeds. The same pending guarantee applies to awaited bumps and coalesced (`request_bump`) bumps. Before awaiting an immediate write, `bump()` MUST consume an older pending marker that the write covers; a marker added while that write is in flight MUST remain queued and produce a later bump. When a write aborts rather than merely failing, the namespace MUST remain pending regardless of whether the database had already accepted its commit. A write that raises MUST NOT prevent the remaining pending namespaces from flushing in the same cycle. When any invalidation callback for a namespace fails, the poller MUST NOT acknowledge the observed version and MUST re-run that namespace's callbacks on subsequent poll cycles until they succeed. The poller MUST escalate consecutive poll failures above debug level after a bounded count (WARNING after 3, ERROR after 10) and increment `codex_lb_cache_invalidation_poll_failures_total`. After a startup baseline read fails, a process that continues without a recorded baseline MUST treat each positive version first observed for a registered namespace by the next successful background poll as changed, run that namespace's registered callbacks, and acknowledge the version only after those callbacks succeed. This recovery MAY cause a redundant invalidation for a version that predates startup; it MUST NOT silently absorb a peer bump into a callback-less baseline.

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
- **WHEN** another mutation commits and requests a bump for the same namespace before that write completes
- **THEN** the new marker survives and is flushed on a subsequent cycle, incrementing the version beyond the in-flight bump

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

- **GIVEN** an awaited or coalesced bump has consumed an earlier pending marker and is awaiting its write
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

### Requirement: Upstream-route resolution is invalidation-driven with a TTL backstop

Proxy hot-path upstream-route resolution MUST be served from a per-account cache of resolver outcomes. Admin mutations of any resolver input (account proxy bindings, proxy pool membership, upstream-proxy dashboard settings, account deletion cascading a binding away) MUST invalidate the cache on the mutating replica before the mutating response returns and durably bump a cache-invalidation namespace so peer replicas converge within one poll interval. The shared bump primitive MUST retain a failed write for retry; route-cache callers MUST NOT add a second pending marker or duplicate namespace solely to compensate for bump failure. The cache TTL MUST default to 60 seconds as a backstop for out-of-band database edits or loss of the originating process, and a TTL of 0 MUST disable caching entirely.

#### Scenario: Repeat turns skip route re-resolution

- **GIVEN** an account whose route resolved less than the TTL ago with no intervening route-input mutation
- **WHEN** another proxy request uses that account
- **THEN** the route MUST be served from the cache without opening a database session

#### Scenario: Binding change invalidates before the response returns

- **GIVEN** a cached route outcome for an account
- **WHEN** an operator upserts that account's proxy binding
- **THEN** the mutating replica's cache MUST be cleared before the HTTP response returns
- **AND** the `upstream_route` namespace MUST be durably bumped so peers clear their caches via the poller

#### Scenario: Pool membership change invalidates

- **GIVEN** a cached route outcome resolved from a pool
- **WHEN** an operator adds a member to any proxy pool
- **THEN** the local cache MUST be cleared and the `upstream_route` namespace durably bumped before the response returns

#### Scenario: Account deletion invalidates

- **GIVEN** a cached route outcome for an account
- **WHEN** an operator deletes the account (cascading its proxy binding away)
- **THEN** the local cache MUST be cleared and the `upstream_route` namespace durably bumped before the response returns

#### Scenario: Peer replicas converge through the poller

- **GIVEN** a cached route outcome on a replica that did not perform the mutation
- **WHEN** the `upstream_route` or `settings` namespace version advances
- **THEN** that replica's cache-invalidation poller MUST clear its route cache within one poll interval

#### Scenario: Upstream settings change invalidates through the settings namespace

- **GIVEN** a cached route outcome
- **WHEN** an operator changes `upstream_proxy_routing_enabled` or `upstream_proxy_default_pool_id`
- **THEN** the mutating replica's route cache MUST be cleared synchronously after the settings commit
- **AND** the durable `settings` namespace bump MUST clear peers' route caches
- **AND** the mutation MUST NOT write a redundant `upstream_route` bump for the same settings change
