# Responses API compatibility delta

## ADDED Requirements

### Requirement: Compact requests preserve scoped turn-state ownership

When a compact request contains a real client-supplied `x-codex-turn-state`, the system MUST resolve the token only in the requesting API key scope and select only that owner account. If the owner cannot be resolved or selected, the request MUST fail closed and MUST NOT fall back to a generic sticky or load-balanced account. Proxy-synthesized first-turn placeholders (the `turn_*` / `http_turn_*` values codex-lb injects when the client did not supply one) are not real continuity tokens until registered as bridge aliases; an unregistered placeholder MUST NOT block file-owner routing, but a registered placeholder MUST still resolve to its owner account.

#### Scenario: Token belongs to the requesting API key

- **GIVEN** an active turn-state owner exists for the requesting API key
- **WHEN** the client submits a compact request with that token
- **THEN** compact selection is constrained to that owner account

#### Scenario: Unscoped sticky state cannot supply a turn-state owner

- **GIVEN** a turn-state token has no owner in the requesting API-key-scoped local or durable bridge indexes
- **WHEN** an unscoped sticky-session mapping exists for the same token
- **THEN** compact owner resolution fails closed
- **AND** the unscoped sticky-session mapping is not consulted

#### Scenario: Token belongs to a different API key or is unavailable

- **GIVEN** the token has no owner in the requesting API key scope
- **WHEN** the client submits a compact request with that token
- **THEN** the request fails with `turn_state_owner_unavailable`
- **AND** no generic account is selected

#### Scenario: Registered synthesized placeholder belongs to the requesting API key

- **GIVEN** a proxy-synthesized `http_turn_*` token has been registered as a bridge alias
- **WHEN** the client later submits a compact request with that token
- **THEN** compact selection is constrained to the registered owner account

#### Scenario: Synthesized first-turn placeholder does not override file-owner routing

- **GIVEN** the request carries only a proxy-synthesized `x-codex-turn-state`
- **AND** the payload references an `input_file.file_id` pinned to an account
- **WHEN** the client submits the compact request
- **THEN** compact routing may use the pinned file owner
- **AND** the synthesized placeholder does not trigger `turn_state_owner_unavailable`

### Requirement: Collected failures retain upstream turn-state metadata

The system MUST copy a real `x-codex-turn-state` received in a `response.metadata` event into the HTTP headers of a collected response, including when the later terminal event is `response.failed`.

#### Scenario: Metadata precedes a failed response

- **GIVEN** a collected response stream emits turn-state metadata
- **AND** the terminal response is failed
- **THEN** the returned HTTP error includes the captured turn-state header
