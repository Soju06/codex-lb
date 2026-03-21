## MODIFIED Requirements

### Requirement: HTTP Responses routes preserve upstream websocket session continuity
HTTP `/v1/responses` and HTTP `/backend-api/codex/responses` MUST preserve upstream websocket session continuity within one live bridge session and MUST distinguish replayed turn-state failure modes cleanly. The service MUST issue signed, versioned `x-codex-turn-state` headers for HTTP bridge continuity, MUST track live bridge ownership durably enough to detect true wrong-instance conflicts across replicas, and MUST recover stale local bridge state by creating a fresh bridge session when the replayed request does not require `previous_response_id` continuity.

#### Scenario: replayed turn-state with live owner on another instance fails closed
- **WHEN** a client replays a valid HTTP bridge turn-state token
- **AND** the durable bridge lease shows a different live owner instance
- **THEN** the service fails the request fast with `bridge_wrong_instance`
- **AND** it MUST NOT create a fresh local bridge session for that token on the wrong instance

#### Scenario: replayed turn-state with expired bridge and no prior-response dependency recovers
- **WHEN** a client replays a valid HTTP bridge turn-state token
- **AND** no live bridge lease exists for that token
- **AND** the request does not include `previous_response_id`
- **THEN** the service creates a fresh bridge session instead of failing with `bridge_instance_mismatch`

#### Scenario: replayed turn-state with expired bridge and prior-response dependency fails clearly
- **WHEN** a client replays a valid HTTP bridge turn-state token
- **AND** no live bridge lease exists for that token
- **AND** the request includes `previous_response_id`
- **THEN** the service fails the request with `bridge_session_expired`

#### Scenario: malformed or forged turn-state token is rejected
- **WHEN** a client sends a replayed HTTP bridge turn-state token that cannot be validated
- **THEN** the service fails the request with `bridge_token_invalid`
