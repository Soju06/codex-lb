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

Slot ownership SHALL be the single authority for who may complete a device flow.
A device `start` claims the slot only while it is still the current local device
flow, so a start superseded on the same replica (its local record already
replaced by a later start) MUST NOT install a stale slot pointer or begin
polling. Because a poll task on another replica cannot be cancelled
cross-process, a poll task MUST atomically consume the slot as its point of no
return, and only the poller that consumed/holds the slot MAY persist an account
OR write ANY terminal status (success or error). A poller that did not win/hold
the slot MUST write nothing, so a losing or duplicate poller that received
`invalid_grant` for the already-consumed code cannot record an `error` during
the winner's persist window. This composes with the atomic monotonic status
write (a durable `success` is never regressed).

The originating replica SHALL be the sole poller for a device flow. A device
`/complete` served on a replica that did not originate the flow MUST report the
durable status through the reconciliation gate and MUST NOT spawn a second poll
task for the single-use device code. If the originating replica dies mid-poll,
the flow expires by its TTL and the user retries.

#### Scenario: Simultaneous device starts leave exactly one current flow

- **GIVEN** two replicas sharing one database
- **WHEN** both start a device-code OAuth flow at the same time
- **THEN** the slot names exactly one of the two `flow_id`s as current
- **AND** only the poll task holding the current slot can consume it and persist;
  the other's consume matches zero rows and it cannot persist

#### Scenario: Overlapping same-replica starts — the later start wins

- **GIVEN** a device `start` is awaiting its durable persist on a replica
- **WHEN** a later device `start` on the same replica supersedes it locally,
  claims the slot, and begins polling
- **THEN** the later start is the current slot holder and the sole poller
- **AND** the superseded earlier start installs no stale slot pointer and starts
  no poll task

#### Scenario: Only the slot holder writes a terminal status

- **GIVEN** the winning poller consumed the slot and is mid-persist (success not
  yet written)
- **WHEN** a losing/duplicate poller receives `invalid_grant` for the consumed
  device code
- **THEN** the loser writes NO terminal status (no `pending` -> `error`)
- **AND** the winner's later `success` is the durable outcome

#### Scenario: Non-originating /complete does not start a second poller

- **GIVEN** a device flow started (and being polled) on its originating replica
- **WHEN** `/complete` for that flow is served on a different replica
- **THEN** that replica reports the durable status and starts no second poll task

### Requirement: Durable status is authoritative over local state at every entry point

Each dashboard OAuth entry point MUST consult the DB-authoritative durable status through one reconciliation gate before it branches on local in-memory flow state.
This covers status polling, `/complete`, the device acknowledgement, the browser
callback handler, and the manual pasted callback. The durable row SHALL always
win over a local `pending`: a
durable terminal (`success` or `error`) overrides local `pending`, and a durable
row that is absent or expired drops the stale local flow. An entry point MUST
NOT branch on a local `pending`, reuse a locally cached PKCE verifier, or replay
a callback without first reconciling against the durable status.

A caller that attempts a durable terminal ERROR write MUST honor a rejected
result. When the monotonic guard rejects a non-success terminal write because
the durable row is already `success` (a racing callback/poller committed success
for the same single-use code), the caller MUST NOT surface an error or leave the
local flow in `error`; it MUST reconcile against the durable row and report the
durable `success`. This applies uniformly to every browser/manual-callback error
branch (invalid callback, `invalid_grant`/`OAuthError` exchange failure, reauth
seat mismatch, identity conflict, and unexpected errors).

#### Scenario: Replayed callback observes the durable terminal instead of re-exchanging

- **GIVEN** replica A started a browser OAuth flow and still holds it locally as
  `pending`
- **AND** the flow was completed on another replica, so the shared DB status is
  `success` (the authorization code is consumed)
- **WHEN** a second browser redirect or a pasted callback for the same `state`
  lands back on replica A
- **THEN** replica A returns the durable `success` and MUST NOT re-exchange the
  already-consumed authorization code
- **AND** replica A reconciles its in-memory flow to `success`

#### Scenario: Every entry point honors a durable terminal over local pending

- **GIVEN** a flow held locally as `pending` on the originating replica whose
  shared-DB status is a terminal written by another replica
- **WHEN** any of status polling, `/complete`, the device acknowledgement, the
  browser callback handler, or the manual callback is invoked for that flow
- **THEN** that entry point reports the durable terminal (never the stale local
  `pending`) and reconciles the local in-memory flow to it

#### Scenario: Loser callback honors durable success on a rejected error write

- **GIVEN** two browser callbacks race on the same single-use authorization code
- **AND** the winner commits durable `success` while the loser is exchanging
- **WHEN** the loser's exchange fails with `invalid_grant` and it attempts a
  durable `error` write that the monotonic guard rejects
- **THEN** the loser reports the durable `success` (not an error) and does not
  leave the local flow in `error`
