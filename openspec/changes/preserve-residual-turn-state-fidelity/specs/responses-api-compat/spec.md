# Responses API compatibility delta

## ADDED Requirements

### Requirement: Compact requests preserve scoped turn-state ownership

When a compact request contains `x-codex-turn-state`, the system MUST resolve
the token only in the requesting API key scope and select only that owner
account. If the owner cannot be resolved or selected, the request MUST fail
closed and MUST NOT fall back to a generic sticky or load-balanced account.

#### Scenario: Token belongs to the requesting API key

- **GIVEN** an active turn-state owner exists for the requesting API key
- **WHEN** the client submits a compact request with that token
- **THEN** compact selection is constrained to that owner account

#### Scenario: Token belongs to a different API key or is unavailable

- **GIVEN** the token has no owner in the requesting API key scope
- **WHEN** the client submits a compact request with that token
- **THEN** the request fails with `turn_state_owner_unavailable`
- **AND** no generic account is selected

### Requirement: Collected failures retain upstream turn-state metadata

The system MUST copy a real `x-codex-turn-state` received in a
`response.metadata` event into the HTTP headers of a collected response,
including when the later terminal event is `response.failed`.

#### Scenario: Metadata precedes a failed response

- **GIVEN** a collected response stream emits turn-state metadata
- **AND** the terminal response is failed
- **THEN** the returned HTTP error includes the captured turn-state header
