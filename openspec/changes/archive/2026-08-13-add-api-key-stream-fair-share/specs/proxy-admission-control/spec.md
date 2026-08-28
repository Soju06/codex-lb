# proxy-admission-control

## ADDED Requirements

### Requirement: Stream admission applies congestion-aware per-API-key fair share

When `proxy_api_key_fair_share_congestion_threshold_pct` is greater than zero, stream-lease selection MUST evaluate a per-API-key fair-share gate over the selection's candidate account set before admitting a stream. Pool capacity MUST be computed as the candidate-account count multiplied by each account's effective stream slots (`max(1, stream_limit - stream_reserve_slots)`), pool in-flight as the sum of the candidate accounts' in-flight stream leases, and both compared with integer arithmetic: the pool is congested if and only if `pool_inflight * 100 >= pool_capacity * threshold_pct`. When the pool is not congested the gate MUST admit unconditionally. When the pool is congested the gate MUST admit a key only if the key's in-flight stream count on the candidate accounts plus one does not exceed `max(2, pool_capacity // active_keys)`, where `active_keys` is the number of API keys holding at least one in-flight stream lease on the candidate accounts with the requester counted exactly once. The gate MUST NOT apply when the configured threshold is zero, when the request carries no API key, when the selection is for a reattach stage, when the lease kind is not stream, or when the effective stream limit is nonpositive; keyless streams MUST still count toward pool in-flight. The gate MUST NOT read the database and MUST evaluate under the same runtime lock that guards lease counters.

#### Scenario: Disabled threshold changes no admission outcome

- **GIVEN** `proxy_api_key_fair_share_congestion_threshold_pct` is 0 (the default)
- **WHEN** any mix of API keys saturates the pool's stream slots
- **THEN** every selection outcome is identical to the behavior before this change

#### Scenario: Uncongested pool admits an already-heavy key

- **GIVEN** a threshold of 80 and pool utilization below 80%
- **AND** one key already holds more streams than `pool_capacity // active_keys`
- **WHEN** that key requests another stream
- **THEN** the request is admitted

#### Scenario: Congested pool denies a key at or above its fair share

- **GIVEN** a threshold of 80 and pool utilization at or above 80%
- **AND** a key holding at least `max(2, pool_capacity // active_keys)` in-flight streams
- **WHEN** that key requests another stream
- **THEN** selection returns the stable reason `api_key_stream_fair_share` and no lease is acquired

#### Scenario: Minimum guarantee admits light keys under congestion

- **GIVEN** a congested pool dominated by another key's streams
- **WHEN** a key holding fewer than two in-flight streams requests a stream
- **THEN** the fair-share gate admits it

#### Scenario: Requester is counted exactly once in the divisor

- **GIVEN** a congested pool where the requester already holds in-flight streams
- **WHEN** the fair share is computed
- **THEN** `active_keys` counts the requester once and does not change whether the requester is currently active or newly arriving

#### Scenario: Keyless requests bypass the gate but consume capacity

- **GIVEN** a congested pool
- **WHEN** a request without an API key selects an account
- **THEN** the fair-share gate does not deny it
- **AND** its in-flight stream counts toward pool in-flight for keyed requesters

#### Scenario: Reattach-stage selection bypasses the gate

- **GIVEN** a congested pool and a heavy key at its fair share
- **WHEN** that key's reattach-stage selection resumes an existing in-flight response
- **THEN** the fair-share gate does not deny it

### Requirement: Fair-share denials reuse local capacity-wait semantics

A fair-share denial MUST surface the stable local-overload reason `api_key_stream_fair_share` and MUST inherit the existing account-capacity handling: the transport layer parks the request with `waiting_for_account_capacity` keepalives and retries selection within the request budget, and a request that exhausts its budget while denied MUST receive HTTP 429 with `error.type` `rate_limit_error` and a `Retry-After` header rather than a 503. The denial message MUST state the key's in-flight count, the fair share, the pool in-flight and capacity, and the active-key count without naming other API keys.

#### Scenario: Denied request parks and admits after the pool decongests

- **GIVEN** a heavy key denied by the fair-share gate
- **WHEN** enough streams release for the key to fall under its fair share or the pool to fall below the threshold
- **THEN** a subsequent parked retry admits the request without client intervention

#### Scenario: Budget exhaustion surfaces 429 with fair-share numbers

- **GIVEN** a request that remains fair-share denied until its budget is exhausted
- **WHEN** the terminal error is rendered
- **THEN** the status is 429 with `error.type` `rate_limit_error` and a `Retry-After` header
- **AND** the message includes the key in-flight count, fair share, pool in-flight, pool capacity, and active-key count

### Requirement: Per-API-key stream accounting follows the lease lifecycle

Every stream lease acquired through account selection MUST record the requesting API key, and the per-account per-key in-flight map MUST be maintained under the runtime lock across acquire, explicit release, and stale reclaim, with map entries removed when a key's count reaches zero and removed together with pruned account runtime state. Account-scoped keys MUST be measured against their scoped candidate accounts only. On the sticky selection path the gate decision MUST be re-validated in the commit lock section before the lease is acquired, so concurrent selections for one key cannot overshoot the share between the filter and commit sections; the unbound path MUST evaluate the gate and acquire the lease in a single lock section.

#### Scenario: Release and stale reclaim decrement the owning key

- **GIVEN** a key holding in-flight stream leases
- **WHEN** a lease is released explicitly or reclaimed as stale
- **THEN** that key's in-flight count decreases accordingly and its map entry is removed at zero

#### Scenario: Scoped key is measured against its scoped pool

- **GIVEN** a key restricted to a subset of accounts via account assignment scope
- **WHEN** the fair-share gate evaluates its request
- **THEN** pool capacity, pool in-flight, and the key's in-flight count are computed over the scoped candidate accounts only

#### Scenario: Concurrent sticky selections cannot overshoot the share

- **GIVEN** a congested pool and one key one stream below its fair share
- **WHEN** two sticky selections for that key pass the filter-phase gate concurrently
- **THEN** at most one acquires a lease and the other is denied at the commit re-check
