# upstream-proxy-routing Delta

## ADDED Requirements

### Requirement: Cached route resolution preserves fail-closed semantics

Any cache in front of upstream-route resolution MUST store the resolver's outcome verbatim — a resolved route, a permitted direct-egress `None`, or a fail-closed error with its reason. A cache hit MUST reproduce that outcome exactly: it MUST NOT convert a fail-closed outcome or a routed outcome into direct egress, and it MUST NOT substitute a different pool or endpoint than the resolver chose. Cache staleness MUST be bounded by invalidation on admin mutations (same-replica: before the mutating response returns; peers: within one cache-invalidation poll interval) with a TTL backstop for out-of-band edits.

#### Scenario: Cached fail-closed outcome keeps failing closed

- **GIVEN** an account-bound pool with no active usable endpoint whose fail-closed resolution outcome is cached
- **WHEN** further upstream operations are attempted for that account
- **THEN** each operation MUST fail before opening an upstream network connection with the same fail-closed reason
- **AND** it MUST NOT use the default pool, environment proxy, or direct egress

#### Scenario: New binding takes effect without a direct-egress window on the mutating replica

- **GIVEN** an account whose cached resolution outcome is direct-egress `None`
- **WHEN** an operator saves an active proxy binding for that account
- **THEN** the mutating replica's cached outcome MUST be invalidated before the binding response returns, so subsequent requests on that replica resolve the bound pool
