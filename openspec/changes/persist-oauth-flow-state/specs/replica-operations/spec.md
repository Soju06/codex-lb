# replica-operations Specification (delta)

## ADDED Requirements

### Requirement: Dashboard OAuth flow state is persisted for cross-replica completion

The dashboard OAuth add-account / reauth flow SHALL persist its per-flow state
(PKCE `code_verifier`, `state` token, method, status, device-code metadata,
intended account id, and timestamps) in the shared database keyed by `flow_id`,
so that a browser callback, a manually pasted callback URL, or a device-code
status poll can be completed by any replica regardless of which replica started
the flow. The PKCE `code_verifier` MUST be encrypted at rest with the same
encryption key material used for account tokens, and abandoned pending flows
MUST expire via a short TTL.

#### Scenario: Callback completes on a replica that did not start the flow

- **GIVEN** two replicas sharing one PostgreSQL database
- **AND** replica A starts a browser OAuth flow, persisting the flow record
- **WHEN** the callback (or manually pasted callback URL) for that `state` token
  lands on replica B, which never held the flow in memory
- **THEN** replica B loads the encrypted verifier and metadata from the shared
  database and completes the authorization-code exchange
- **AND** the added or re-authenticated account is persisted

#### Scenario: Status poll reflects a completion written by another replica

- **GIVEN** replica A started an OAuth flow and still holds it in memory as
  `pending`
- **AND** replica B completed the same flow and wrote `success` to the shared
  database
- **WHEN** the dashboard polls `GET /api/oauth/status` for that `flow_id` and the
  request lands on replica A
- **THEN** replica A returns the authoritative `success` status from the shared
  database rather than its stale in-memory `pending`

#### Scenario: Complete honors a durable terminal written by another replica

- **GIVEN** replica A started a browser OAuth flow and still holds it in memory
  as `pending`
- **AND** replica B completed the same flow and wrote `success` (or `error`) to
  the shared database
- **WHEN** the dashboard calls `POST /api/oauth/complete` for that `flow_id` and
  the request lands on replica A
- **THEN** replica A returns the authoritative terminal status from the shared
  database rather than its stale in-memory `pending`
- **AND** replica A reconciles its in-memory flow state to that terminal status

#### Scenario: A durable success is never regressed to error

- **GIVEN** a persisted flow whose shared-database status is `success`
- **WHEN** a later status write attempts to set the same `flow_id` to `error`
  (e.g. a duplicate or losing device poller receiving an OAuth error for the
  already-consumed device code)
- **THEN** the persisted `success` status is retained and MUST NOT be overwritten
- **AND** status polling continues to report `success`

#### Scenario: Device-code acknowledgement does not re-poll a completed flow

- **GIVEN** a device-code flow whose in-process poller has already reached a
  terminal status
- **WHEN** `POST /api/oauth/complete` is called for that flow
- **THEN** no second poll of the single-use device code is started
- **AND** the untargeted acknowledgement (no `flow_id`) reports `pending` while a
  targeted call (explicit `flow_id`) reports the durable terminal status

#### Scenario: Abandoned pending flow expires

- **GIVEN** a persisted pending flow whose `expires_at` is in the past
- **WHEN** a replica reads that flow by `flow_id` or `state` token
- **THEN** the expired pending flow is treated as absent
- **AND** it is purged opportunistically so it cannot complete after its TTL
