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

#### Scenario: durable recovery prefers the last successful account before general fallback
- **WHEN** a bridged follow-up request recovers from durable continuity metadata after owner loss or owner-forward failure
- **AND** the durable continuity record retains the last successful upstream `account_id`
- **THEN** the service attempts reattach on that account before selecting a different account
- **AND** only falls back to the broader account pool if the preferred account is out of scope or fails the bounded same-account reconnect attempt

### Requirement: Durable bridge ownership MUST use a lease with epoch fencing

The replica executing a bridged HTTP session MUST publish its ownership in durable storage using a renewable lease and monotonically increasing epoch so stale owners can be superseded safely after restart or drain.

#### Scenario: draining owner releases lease for takeover
- **WHEN** a replica begins shutdown drain for live HTTP bridge sessions
- **THEN** its durable session rows are marked draining
- **AND** the lease is released when those local sessions are closed
- **AND** another replica may claim execution for the next valid turn using a higher or renewed durable owner epoch

### Requirement: Public `/v1/responses` MUST preserve a deterministic JSON-or-SSE contract across owner handoff and durable recovery

When a request traverses owner-forward, durable fresh reattach, or local live-session reuse, the public edge MUST still emit a valid non-stream JSON body or a valid streaming terminal event sequence.

#### Scenario: non-stream follow-up normalizes unknown output items
- **WHEN** a non-stream follow-up turn completes after durable recovery or owner-forward
- **AND** the upstream response includes output items that are not part of the gateway-safe public contract
- **THEN** the edge normalizes or drops those items before returning the response body
- **AND** the client receives valid JSON instead of an empty body or malformed payload

#### Scenario: stream terminates without a valid terminal response event
- **WHEN** a streaming `/v1/responses` request ends after malformed SSE data or early upstream EOF
- **THEN** the edge emits a terminal `response.failed` event
- **AND** it does not expose malformed SSE data or an unterminated stream to the client

### Requirement: Bridge-enabled readiness MUST wait for durable registration completion

When the HTTP bridge is enabled, the service MUST not report ready until bridge registration has completed and the instance can safely participate in continuity ownership.

#### Scenario: startup has not completed bridge registration yet
- **WHEN** `/health/ready` is called while bridge registration is still incomplete
- **THEN** the service returns `503`
- **AND** does not report ready merely because the database probe succeeds

### Requirement: Ingress-backed `/responses` traffic MUST use session-scoped sticky routing by default

When the chart renders ingress resources for `/responses`, the sticky routing key MUST not pin all conversations for one API key to the same replica.

#### Scenario: dedicated responses ingress prefers session-scoped stickiness
- **WHEN** ingress is enabled for `/v1/responses` or `/backend-api/codex/responses`
- **THEN** the rendered responses ingress hashes by `x-codex-session-id`
- **AND** it does not rely solely on `Authorization` for those paths

### Requirement: Bundled/local installs MUST not require startup migrations to reach a stable schema

Bundled or smoke installs MUST reach a stable serving state without relying on app startup to mutate the schema.

#### Scenario: bundled install waits for explicit schema migration
- **WHEN** the bundled install path is used
- **THEN** startup migration is disabled for the serving pods
- **AND** schema migration happens explicitly before the application starts accepting traffic

### Requirement: Restored encrypted accounts MUST decrypt on fresh installs

When encrypted account tokens are restored into a fresh deployment, the application MUST use the mounted encryption key file instead of attempting to create a new local key path on the read-only filesystem.

#### Scenario: serving pod starts with restored encrypted accounts
- **WHEN** the deployment mounts the secret-backed encryption key
- **THEN** the application container exports `CODEX_LB_ENCRYPTION_KEY_FILE`
- **AND** token decryption does not fail due to a generated fallback path on the read-only root filesystem
