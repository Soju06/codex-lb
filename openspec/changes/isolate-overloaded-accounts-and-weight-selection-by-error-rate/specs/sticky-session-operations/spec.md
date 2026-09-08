# sticky-session-operations Delta

## ADDED Requirements

### Requirement: Isolated accounts release their soft sticky owners

While an account is in the overload **isolation** stage (see `account-routing`), a `prompt_cache`, `sticky_thread` or `codex_session` mapping pinned to it MUST be treated as a fresh admission: selection MUST evaluate the overload-free candidates with the configured strategy and, when one is selectable, MUST route the request there and rebind the mapping to the selected account so later turns do not return to the isolated owner. When no overload-free candidate is selectable (lone account, every sibling backed off, or the strategy rejects the overload-free pool) the pinned owner MUST be kept. A soft backoff below the isolation stage MUST NOT release an established owner. Required owners resolved from hard continuity sources (`previous_response_id`, live or durable bridge ownership, file pins, turn-state rows) MUST NOT be released by this rule. A process-session preference for a brand-new thread MUST be skipped only while the preferred account is in overload backoff and the configured strategy selects an overload-free candidate; when no such candidate is selectable the preference MUST be honored. The service MUST emit an internal `sticky_owner_overload_isolation_reroute` diagnostic for each release without adding it to the stable failure taxonomy; the diagnostic MUST NOT include account identifiers.

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
