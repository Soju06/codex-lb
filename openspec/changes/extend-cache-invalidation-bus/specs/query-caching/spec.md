# query-caching Delta

## ADDED Requirements

### Requirement: Cross-replica cache invalidation bus bounds process-local cache staleness
Every process-local cache that serves security, authorization, or routing decisions MUST either register a namespace on the cross-replica cache-invalidation bus or declare a documented maximum cross-replica staleness TTL. Mutations MUST commit durable state before (or independently of) bumping their namespace version, each process MUST poll the `cache_invalidation` version table at a bounded interval (default 0.5s) and run registered namespace callbacks on version change, and the registered namespaces MUST include `api_key`, `firewall`, `account_routing`, `account_selection`, and `settings`. A cache's TTL remains the fallback staleness bound when a bump is lost.

#### Scenario: Selection-state change on one replica converges on peers within the bus bound

- **GIVEN** two replicas share one database and each runs the cache-invalidation poller
- **AND** replica B holds warm cached selection inputs that include account X
- **WHEN** replica A persists a state change for account X and invalidates its selection cache with propagation
- **THEN** the `account_selection` namespace version is bumped within one poll cycle
- **AND** replica B's selection cache is invalidated on its next poll, without waiting for the cache TTL

#### Scenario: New security-relevant cache without bus coverage is a spec violation

- **GIVEN** a contributor adds a new process-local cache that gates a security, authorization, or routing decision
- **WHEN** the cache neither registers a cache-invalidation namespace nor documents a maximum cross-replica staleness TTL
- **THEN** the change violates this capability and is rejected at review

#### Scenario: Lost bump still converges within the fallback TTL

- **GIVEN** a mutation's namespace bump is permanently lost after retries
- **WHEN** peer replicas keep serving their cached values
- **THEN** each peer converges no later than that cache's documented fallback TTL

### Requirement: Cache invalidation bumps and polling are resilient and observable
`bump()` MUST retry transient write failures (including SQLite "database is locked") with a short backoff; on final failure it MUST log at ERROR with the namespace, increment `codex_lb_cache_invalidation_bump_failures_total{namespace}`, and MUST NOT fail the originating mutation. Coalesced (`request_bump`) namespaces MUST remain pending and be retried on subsequent poll cycles until a bump succeeds. The poller MUST escalate consecutive poll failures above debug level after a bounded count (WARNING after 3, ERROR after 10) and increment `codex_lb_cache_invalidation_poll_failures_total`.

#### Scenario: Bump failure under database lock is observable and does not fail the mutation

- **GIVEN** the database rejects cache-invalidation writes with a lock error for longer than the retry budget
- **WHEN** a mutation attempts a durable namespace bump
- **THEN** the mutation itself still succeeds
- **AND** an ERROR log naming the namespace is emitted and the bump-failure counter increments

#### Scenario: Pending coalesced namespace flushes on the next successful cycle

- **GIVEN** a coalesced `request_bump` namespace failed to flush during a poll cycle
- **WHEN** the database becomes writable again
- **THEN** the next poll cycle flushes the pending namespace and increments its version

#### Scenario: Consecutive poll failures escalate above debug

- **GIVEN** a replica's poller cannot read the `cache_invalidation` table
- **WHEN** three consecutive polls fail
- **THEN** a WARNING is logged and the poll-failure counter increments
