## ADDED Requirements

### Requirement: Responses requests revalidate selected accounts before use

Before returning a selected upstream account to a Responses request path, the service MUST discard that selection and retry if concurrent runtime state changes make the selected account no longer current or eligible.

#### Scenario: Concurrent permanent failure invalidates a selected account

- **WHEN** account selection chooses an `ACTIVE` account
- **AND** a concurrent refresh/deactivation path marks that same account `DEACTIVATED` before the selection is returned
- **THEN** the service retries selection against fresh inputs instead of returning the stale account

#### Scenario: Concurrent quota update invalidates a selected account

- **WHEN** account selection chooses an `ACTIVE` account
- **AND** a concurrent quota update marks that same account `QUOTA_EXCEEDED` before the selection is returned
- **THEN** the service retries selection against fresh inputs instead of returning the stale account

### Requirement: Websocket half-open probes stay single-flight until lifecycle completion

When an account circuit breaker is `HALF_OPEN`, the probe slot for an upstream websocket request MUST remain held until the websocket lifecycle records success or failure. A successful handshake alone MUST NOT release the probe slot.

#### Scenario: Half-open websocket handshake does not admit a second probe

- **WHEN** a websocket request is admitted as the current half-open probe
- **AND** its handshake succeeds with `101 Switching Protocols`
- **AND** the websocket lifecycle has not yet produced a terminal outcome
- **THEN** a second concurrent websocket request for that account is rejected by the circuit breaker

### Requirement: Circuit-breaker-open HTTP requests release request context eagerly

If an aiohttp request context manager has already been created for an upstream Responses or transcription call, and circuit-breaker admission rejects the call before entering that context manager, the service MUST close that request object eagerly so no un-awaited request coroutine is leaked.

#### Scenario: Open circuit rejects an aiohttp request without leaking the request coroutine

- **WHEN** the service creates an aiohttp request context manager
- **AND** the account circuit breaker rejects the call before the context manager is entered
- **THEN** the request object is closed eagerly
- **AND** no un-awaited aiohttp request warning is emitted

### Requirement: HTTP bridge drain preserves existing live session continuity

When HTTP bridge drain is active, the service MUST reject creation of new bridge sessions but MUST continue to reuse an already-live compatible bridge session for follow-up requests.

#### Scenario: Drain allows reuse of an existing live bridge session

- **WHEN** bridge drain is active
- **AND** a compatible live HTTP bridge session already exists for the request key
- **THEN** the service reuses that session instead of returning `bridge_drain_active`

#### Scenario: Drain still rejects new bridge session creation

- **WHEN** bridge drain is active
- **AND** no compatible live HTTP bridge session exists for the request key
- **THEN** the service returns `bridge_drain_active`
