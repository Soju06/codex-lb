## REMOVED Requirements

### Requirement: Isolated accounts release their soft sticky owners

**Reason**: the release is no longer a rebind, so the requirement is replaced by "Isolated accounts release their soft sticky owners for one request" below. Every scenario is carried over; the two that asserted a rebind ("Isolated owner is released to an overload-free sibling", "Capped and isolated owner is rebound to the spillover target") are restated as retention, which is a rename, and the rest are unchanged.

**Migration**: none. No setting, schema or API surface changes; the mapping simply survives the isolation episode.

## ADDED Requirements

### Requirement: Isolated accounts release their soft sticky owners for one request

While an account is in the overload **isolation** stage (see `account-routing`), a `prompt_cache`, `sticky_thread` or `codex_session` mapping pinned to it MUST be treated as a fresh admission: selection MUST evaluate the overload-free candidates with the configured strategy and, when one is selectable, MUST route the request there. That release MUST be **request-local**: the mapping MUST NOT be deleted or rebound, so a later turn returns to the owner once isolation lifts. The replacement MUST be stable for the duration of the episode: the configured strategy's pick over the overload-free pool MUST be seeded by the mapping and its owner, so the same mapping resolves to the same replacement on every turn -- including across turns the mapping itself has already been admitted on -- and replicas observing the same pool agree, independent of the order in which candidates are presented. That seeded pick MUST be made among the candidates the configured strategy would otherwise draw from, after every eligibility filter and narrowing it applies -- budget tier, health tier, routing policy, quota, cooldown, error backoff, planner cost, reset preference, and positive draw weight -- so a replacement can never be an account the strategy would have refused, and the seed can only express a preference among accounts it already accepts. Within that set the choice MUST NOT depend on quantities that move while the episode runs (remaining credits, elapsed reset time, recent error rate, last-selected time), nor on a strategy's relative *ranking* of candidates, which reorders when any account refreshes, or a thread would be one refresh away from losing its substitute; unseeded selection MUST keep the configured strategy's full weighted draw. Request-local retention MUST apply only while the pinned owner's persisted status is `active`, `reauth_required`, `rate_limited` or `quota_exceeded`; a `paused` or `deactivated` owner, an owner that has left the request's routable pool entirely (removed, out of API-key scope, or not authorized for the request), and a request that explicitly reallocates the mapping MUST still rebind it on that turn, and the diagnostic MUST record the mapping as rebound in that case. An isolated owner that is additionally unavailable for request-local reasons only -- filtered by the per-account concurrency caps, or excluded by this request's own retry loop while it stays recoverable and within the request's continuity scope -- MUST keep its mapping, exactly as it already does when it is not isolated: isolation MUST NOT convert a request-local spillover into a rebind. On a kind bounded by an affinity TTL (`prompt_cache`), retention MUST keep the retained mapping alive: each retained turn MUST rewrite the mapping onto its own owner rather than writing nothing, including when the owner never reached selection because a concurrency cap or this request's exclusion list removed it. A mapping preserved for any other reason -- notably an ambiguous conversation owner, which may sit outside the request's routable or security scope -- MUST NOT be refreshed or counted as an isolation release. A seeded replacement MUST NOT carry a recovery probe admission, whose whole purpose is to move between probing accounts. Spillover that is not an isolation release MUST keep the configured strategy's ordinary draw, including its recovery probes: stability is bought only where an owner is actually being held through an isolation window. Retention MUST NOT depend on the isolation window being shorter than the TTL. That rewrite MUST NOT rebind the mapping and MUST remain subject to the existing same-owner refresh skip window. When no overload-free candidate is selectable (lone account, every sibling backed off, or the strategy rejects the overload-free pool) the pinned owner MUST be kept. A soft backoff below the isolation stage MUST NOT release an established owner. When the released owner is also above the sticky reallocation budget threshold, the replacement MUST be chosen with the secondary-budget filter applied, as for a budget reallocation. A replacement chosen for a released owner MUST satisfy the per-account concurrency caps even where the owner itself is cap-exempt (bare `codex_session` mapping without cap spillover). A bare `codex_session` owner that is both at its account cap with spillover enabled and isolated MUST keep its mapping, as a capped-but-not-isolated owner already does. Required owners resolved from hard continuity sources (`previous_response_id`, live or durable bridge ownership, file pins, turn-state rows) MUST NOT be released by this rule. A process-session preference for a brand-new thread MUST be skipped only while the preferred account is in overload backoff and the configured strategy selects an overload-free candidate; when no such candidate is selectable the preference MUST be honored. The service MUST emit an internal `sticky_owner_overload_isolation_reroute` diagnostic for each release, including a release of an owner that never reached selection -- because a cap or this request's exclusion list removed it, because it was paused or deactivated, or because it was outside the request's scope -- without adding it to the stable failure taxonomy; the diagnostic MUST record whether the mapping was retained or rebound, MUST be emitted only once the replacement has actually been admitted -- not merely selected, since a candidate can still be lost to a concurrent lease or the attempt retried on stale state -- exactly once per served release, and MUST NOT include account identifiers.

#### Scenario: Isolated owner is released to an overload-free sibling

- **GIVEN** a `prompt_cache` session pinned to account A, which is isolated for overload
- **AND** account B is selectable and not in overload backoff
- **WHEN** the next request on that session selects an account
- **THEN** account B serves the request and the mapping still points to account A
- **AND** the only sticky write is a refresh of that mapping onto account A, and no row is deleted
- **AND** the probe reservation pool is the overload-free pool the pick came from
- **AND** the `sticky_owner_overload_isolation_reroute` diagnostic records the mapping as retained

#### Scenario: Soft backoff keeps the warm owner


- **GIVEN** a session pinned to account A, which is in soft overload backoff below the isolation level
- **WHEN** the next request selects an account
- **THEN** account A keeps the session

#### Scenario: Isolated owner is kept when nothing else is selectable


- **GIVEN** a session pinned to isolated account A whose only sibling is rate-limited
- **WHEN** the next request selects an account
- **THEN** account A keeps the session rather than failing the request

#### Scenario: Isolated owner is not released to a saturated sibling


- **GIVEN** a bare `codex_session` mapping pinned to isolated account A with cap spillover disabled
- **AND** the only sibling B is at its stream cap
- **WHEN** the next request selects an account with a stream lease
- **THEN** account A serves the request (its cap exemption is kept) and no `account_stream_cap` error is returned

#### Scenario: Capped and isolated owner keeps its request-local spillover

- **GIVEN** a bare `codex_session` mapping pinned to account A, which is at its stream cap with spillover enabled and is isolated
- **WHEN** the next request spills to sibling B
- **THEN** the mapping still points to account A and no sticky row is written or deleted, exactly as for a capped-but-not-isolated owner

#### Scenario: One isolation episode yields one replacement

- **GIVEN** a `prompt_cache` session pinned to isolated account A with several selectable overload-free siblings
- **WHEN** the session issues many turns while the isolation holds
- **THEN** every turn is served by the same sibling and the mapping is never rebound or deleted
- **AND** a session with a different sticky key over the same pool may resolve to a different sibling

#### Scenario: An isolated owner that is no longer recoverable is released

- **GIVEN** a `prompt_cache` session pinned to isolated account A whose persisted status is `deactivated`
- **AND** account B is selectable
- **WHEN** the next request on that session selects an account
- **THEN** account B is selected and the mapping is rebound to B on that turn

#### Scenario: A hard continuity owner is untouched by isolation

- **GIVEN** a hard `codex_session` mapping pinned to account A, which is isolated for overload
- **WHEN** the next request on that session selects an account
- **THEN** account A serves the request and no sticky row is written or deleted

#### Scenario: Explicit reallocation still retires an isolated owner

- **GIVEN** a `prompt_cache` session pinned to isolated account A and a selectable sibling B
- **WHEN** a request on that session selects an account with sticky reallocation requested
- **THEN** account B is selected and the mapping is rebound to B
- **AND** the `sticky_owner_overload_isolation_reroute` diagnostic records the mapping as rebound, after the replacement is selected

#### Scenario: Isolated owner excluded by the retry loop keeps its mapping

- **GIVEN** a `prompt_cache` thread mapping points to account A, which is isolated and whose persisted status is `active`
- **AND** this request's retry loop already excluded account A after a transient upstream failure
- **AND** account B is eligible
- **WHEN** the retry re-selects an account
- **THEN** account B serves the request and the mapping still points to account A

#### Scenario: A retained mapping outlives the isolation window

- **GIVEN** a `prompt_cache` session pinned to isolated account A, an affinity TTL equal to the isolation window, and a selectable sibling B
- **AND** account A is absent from selection because a concurrency cap or this request's exclusion list removed it
- **WHEN** the session keeps issuing turns until the TTL would otherwise have elapsed
- **THEN** each turn refreshes the mapping onto account A
- **AND** the mapping still points to account A when isolation lifts

#### Scenario: A stable replacement is still subject to the strategy's pool-relative filters

- **GIVEN** a `prompt_cache` session pinned to isolated account A
- **AND** the overload-free pool holds a healthy sibling alongside one that is draining, one whose routing policy is `preserve`, and one above the budget threshold
- **WHEN** turns are served while the isolation holds
- **THEN** the healthy, budget-safe, `normal`-policy sibling serves them
- **AND** no turn is served by a sibling the strategy would have refused had it picked over the whole pool

#### Scenario: A release of an off-pool isolated owner is counted

- **GIVEN** a `prompt_cache` session pinned to isolated account A, removed from selection by a concurrency cap or this request's exclusion list
- **WHEN** sibling B serves the turn and the mapping is kept
- **THEN** the `sticky_owner_overload_isolation_reroute` diagnostic records the mapping as retained, with no account identifiers

#### Scenario: A release the selector never saw a state for is counted

- **GIVEN** a `prompt_cache` session pinned to isolated account A, dropped before states are built because it is paused
- **WHEN** sibling B is selected and the mapping is rebound to B
- **THEN** the `sticky_owner_overload_isolation_reroute` diagnostic records the mapping as rebound, with no account identifiers

#### Scenario: Distinct threads spread over an equally scored pool

- **GIVEN** twelve equally scored overload-free siblings and a strategy that admits only its top five
- **WHEN** many isolated threads are each served a turn
- **THEN** more than five distinct siblings serve them
- **AND** each individual thread is served by the same sibling every turn
