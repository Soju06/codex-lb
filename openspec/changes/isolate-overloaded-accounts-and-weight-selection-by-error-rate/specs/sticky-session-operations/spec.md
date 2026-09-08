# sticky-session-operations Delta

## ADDED Requirements

### Requirement: Isolated accounts release their soft sticky owners

While an account is in the overload **isolation** stage (see `account-routing`), a `prompt_cache`, `sticky_thread` or `codex_session` mapping pinned to it MUST be treated as a fresh admission: selection MUST evaluate the overload-free candidates with the configured strategy and, when one is selectable, MUST route the request there and rebind the mapping to the selected account so later turns do not return to the isolated owner. When no overload-free candidate is selectable (lone account, every sibling backed off, or the strategy rejects the overload-free pool) the pinned owner MUST be kept. A soft backoff below the isolation stage MUST NOT release an established owner. When the released owner is also above the sticky reallocation budget threshold, the replacement MUST be chosen with the secondary-budget filter applied, as for a budget reallocation. A replacement chosen for a released owner MUST satisfy the per-account concurrency caps even where the owner itself is cap-exempt (bare `codex_session` mapping without cap spillover). A bare `codex_session` owner that is both at its account cap with spillover enabled and isolated MUST be rebound to the spillover target rather than preserving the mapping request-locally. Required owners resolved from hard continuity sources (`previous_response_id`, live or durable bridge ownership, file pins, turn-state rows) MUST NOT be released by this rule. A process-session preference for a brand-new thread MUST be skipped only while the preferred account is in overload backoff and the configured strategy selects an overload-free candidate; when no such candidate is selectable the preference MUST be honored. The service MUST emit an internal `sticky_owner_overload_isolation_reroute` diagnostic for each release without adding it to the stable failure taxonomy; the diagnostic MUST NOT include account identifiers.

#### Scenario: Isolated owner is released to an overload-free sibling

- **GIVEN** a `prompt_cache` session pinned to account A, which is isolated for overload
- **AND** account B is selectable and not in overload backoff
- **WHEN** the next request on that session selects an account
- **THEN** account B is selected and the mapping is rebound to B
- **AND** the probe reservation pool is the overload-free pool the pick came from

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

#### Scenario: Capped and isolated owner is rebound to the spillover target

- **GIVEN** a bare `codex_session` mapping pinned to account A, which is at its stream cap with spillover enabled and is isolated
- **WHEN** the next request spills to sibling B
- **THEN** the mapping is rebound to B (a capped-but-not-isolated owner keeps the request-local spillover and preserves the mapping)
