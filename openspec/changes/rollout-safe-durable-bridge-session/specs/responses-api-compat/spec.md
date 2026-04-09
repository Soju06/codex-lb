## ADDED Requirements

### Requirement: HTTP bridge continuity metadata MUST survive owner loss in durable storage

When the HTTP `/responses` bridge is enabled, the service MUST persist canonical continuity records and alias mappings in shared database storage so that continuity recovery does not depend solely on one replica retaining in-memory session state.

#### Scenario: turn-state and previous-response aliases resolve to the canonical bridged session
- **WHEN** a bridged HTTP session emits `x-codex-turn-state` and upstream `response.id` values
- **THEN** the service stores alias mappings for those values in shared durable storage scoped by API key identity
- **AND** a later request may resolve the same canonical bridged session by `x-codex-turn-state`, `previous_response_id`, or stable `x-codex-session-id`

### Requirement: HTTP bridge MUST support fresh upstream reattach from the latest durable response anchor

When a bridged HTTP request arrives for a valid continuity key but the owning replica no longer has a live in-memory upstream websocket, the service MUST attempt recovery using the latest durable `response.id` anchor instead of immediately failing continuity.

#### Scenario: request omits previous_response_id but replays a valid turn-state
- **WHEN** a client sends a follow-up HTTP request with a valid `x-codex-turn-state`
- **AND** the durable continuity record has `latest_response_id`
- **AND** no live in-memory session is available on the current replica
- **THEN** the service injects the durable `latest_response_id` as the replay anchor for a fresh upstream request
- **AND** the request continues without returning `previous_response_not_found`

#### Scenario: owner-forward failure falls back to durable reattach
- **WHEN** a hard-affinity bridged request resolves to another owner replica
- **AND** owner-forward fails or the owner endpoint is unavailable
- **AND** the durable continuity record has a replayable `latest_response_id`
- **THEN** the service attempts local fresh upstream reattach using the durable continuity record
- **AND** does not fail with `bridge_owner_unreachable` if the recovery succeeds

### Requirement: Durable bridge ownership MUST use a lease with epoch fencing

The replica executing a bridged HTTP session MUST publish its ownership in durable storage using a renewable lease and monotonically increasing epoch so stale owners can be superseded safely after restart or drain.

#### Scenario: draining owner releases lease for takeover
- **WHEN** a replica begins shutdown drain for live HTTP bridge sessions
- **THEN** its durable session rows are marked draining
- **AND** the lease is released when those local sessions are closed
- **AND** another replica may claim execution for the next valid turn using a higher or renewed durable owner epoch
