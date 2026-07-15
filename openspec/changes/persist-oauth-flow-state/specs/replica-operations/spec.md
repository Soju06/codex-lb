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
MUST expire via a short TTL. The TTL MUST be enforced uniformly on every
replica, including the originating replica that still holds the flow in local
memory.

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

#### Scenario: Expired flow is rejected uniformly on the originating replica

- **GIVEN** replica A started a browser OAuth flow and still holds its state
  (including the cached PKCE verifier) in memory
- **AND** the flow's TTL has elapsed
- **WHEN** the browser callback or a manually pasted callback URL for that flow
  lands on replica A
- **THEN** replica A rejects it as expired / state-mismatch and MUST NOT complete
  the authorization-code exchange from the stale cached verifier
- **AND** the outcome matches a replica without local state (where the durable
  row is classified expired on read), so the TTL holds uniformly

### Requirement: At most one device-code OAuth flow is active, enforced atomically

The dashboard device-code OAuth flow SHALL be coordinated as a single active
"slot" in the shared database so that at most one device flow is current at a
time, and replacement SHALL be atomic. A device `start` MUST claim the slot with
a single conditional UPSERT (not a delete-then-insert), so two replicas starting
device OAuth simultaneously leave exactly ONE current `flow_id` rather than two
orphaned pending records that both believe they are current.

Because a poll task running on another replica cannot be cancelled
cross-process, a device poll task MUST atomically consume the slot as its point
of no return immediately before persisting tokens, and MUST persist the account
only if that consume succeeded. A poll task whose flow was superseded (the slot
now names a different `flow_id`) MUST lose the consume and abort WITHOUT adding
or re-authenticating an account, leaving no window between the liveness check
and the account write through which a replacement can slip. This composes with
the atomic monotonic status write (a durable `success` is never regressed).

#### Scenario: Simultaneous device starts leave exactly one current flow

- **GIVEN** two replicas sharing one database
- **WHEN** both start a device-code OAuth flow at the same time
- **THEN** the slot names exactly one of the two `flow_id`s as current
- **AND** only the poll task holding the current slot can consume it and persist;
  the other's consume matches zero rows and it cannot persist

#### Scenario: Superseded device poll task cannot persist (liveness race)

- **GIVEN** a device poll task has exchanged its code and is about to persist
- **AND** a newer device `start` atomically re-claims the slot to a different
  `flow_id` before the first task's account write commits
- **THEN** the superseded task's slot consume matches zero rows
- **AND** it aborts without adding or re-authenticating an account, and writes no
  terminal status for the superseded flow
