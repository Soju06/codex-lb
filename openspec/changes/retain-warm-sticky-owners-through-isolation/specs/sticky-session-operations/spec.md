## REMOVED Requirements

### Requirement: Isolated accounts release their soft sticky owners

**Reason**: the release is no longer a rebind, so the requirement is replaced by "Isolated accounts release their soft sticky owners for one request" below. Every scenario is carried over; the two that asserted a rebind ("Isolated owner is released to an overload-free sibling", "Capped and isolated owner is rebound to the spillover target") are restated as retention, which is a rename, and the rest are unchanged.

**Migration**: none. No setting, schema or API surface changes; the mapping simply survives the isolation episode.

## ADDED Requirements

### Requirement: Isolated accounts release their soft sticky owners for one request

While an account is in the overload **isolation** stage (see `account-routing`), a `prompt_cache`, `sticky_thread` or `codex_session` mapping pinned to it MUST be treated as a fresh admission: selection MUST evaluate the overload-free candidates with the configured strategy and, when one is selectable, MUST route the request there. That release MUST be **request-local**: the mapping MUST NOT be deleted or rebound, so a later turn returns to the owner once isolation lifts. The replacement MUST be derived deterministically from the sticky key over the overload-free pool, independent of the order in which candidates are presented, so the same mapping resolves to the same replacement on every turn of an isolation episode and replicas observing the same pool agree; the configured strategy MUST still judge that replacement's eligibility, and selection MUST fall back to an ordinary pick from the overload-free pool when it rejects the derived one. Request-local retention MUST apply only while the pinned owner's persisted status is `active`, `reauth_required`, `rate_limited` or `quota_exceeded`; a `paused` or `deactivated` owner, an owner that has left the request's routable pool entirely (removed, out of API-key scope, or not authorized for the request), and a request that explicitly reallocates the mapping MUST still rebind it on that turn, and the diagnostic MUST record the mapping as rebound in that case. An isolated owner that is additionally unavailable for request-local reasons only -- filtered by the per-account concurrency caps, or excluded by this request's own retry loop while it stays recoverable and within the request's continuity scope -- MUST keep its mapping, exactly as it already does when it is not isolated: isolation MUST NOT convert a request-local spillover into a rebind. When no overload-free candidate is selectable (lone account, every sibling backed off, or the strategy rejects the overload-free pool) the pinned owner MUST be kept. A soft backoff below the isolation stage MUST NOT release an established owner. When the released owner is also above the sticky reallocation budget threshold, the replacement MUST be chosen with the secondary-budget filter applied, as for a budget reallocation. A replacement chosen for a released owner MUST satisfy the per-account concurrency caps even where the owner itself is cap-exempt (bare `codex_session` mapping without cap spillover). A bare `codex_session` owner that is both at its account cap with spillover enabled and isolated MUST keep its mapping, as a capped-but-not-isolated owner already does. Required owners resolved from hard continuity sources (`previous_response_id`, live or durable bridge ownership, file pins, turn-state rows) MUST NOT be released by this rule. A process-session preference for a brand-new thread MUST be skipped only while the preferred account is in overload backoff and the configured strategy selects an overload-free candidate; when no such candidate is selectable the preference MUST be honored. The service MUST emit an internal `sticky_owner_overload_isolation_reroute` diagnostic for each release without adding it to the stable failure taxonomy; the diagnostic MUST record that the mapping was retained and whether the derived or the fallback replacement was used, and MUST NOT include account identifiers.

#### Scenario: Isolated owner is released to an overload-free sibling

- **GIVEN** a `prompt_cache` session pinned to account A, which is isolated for overload
- **AND** account B is selectable and not in overload backoff
- **WHEN** the next request on that session selects an account
- **THEN** account B serves the request and the mapping still points to account A
- **AND** no sticky row is written or deleted
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
- **THEN** every turn is served by the same sibling and no sticky row is written or deleted
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
- **AND** the `sticky_owner_overload_isolation_reroute` diagnostic records the mapping as rebound

#### Scenario: Isolated owner excluded by the retry loop keeps its mapping

- **GIVEN** a `prompt_cache` thread mapping points to account A, which is isolated and whose persisted status is `active`
- **AND** this request's retry loop already excluded account A after a transient upstream failure
- **AND** account B is eligible
- **WHEN** the retry re-selects an account
- **THEN** account B serves the request and the mapping still points to account A
