# query-caching Delta

## ADDED Requirements

### Requirement: Upstream-route resolution is invalidation-driven with a TTL backstop

Proxy hot-path upstream-route resolution MUST be served from a per-account cache of resolver outcomes. Admin mutations of any resolver input (account proxy bindings, proxy pool membership, upstream-proxy dashboard settings, account deletion cascading a binding away) MUST invalidate the cache on the mutating replica before the mutating response returns and durably bump a cache-invalidation namespace so peer replicas converge within one poll interval. If the durable bump write fails (the bump primitive is non-raising), the implementation MUST enqueue the coalesced retry so peers still converge on the first poll cycle after the write path recovers. The cache TTL MUST default to 60 seconds as a backstop for out-of-band database edits, and a TTL of 0 MUST disable caching entirely.

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

#### Scenario: Upstream settings change invalidates

- **GIVEN** a cached route outcome
- **WHEN** an operator changes `upstream_proxy_routing_enabled` or `upstream_proxy_default_pool_id`
- **THEN** the mutating replica's route cache MUST be cleared and the `upstream_route` namespace durably bumped (with the coalesced retry on write failure) before the response returns
- **AND** peers MUST also clear theirs via the durable `settings` namespace bump
