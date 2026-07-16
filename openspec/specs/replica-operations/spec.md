# replica-operations Specification

## Purpose

Define the supported multi-replica deployment topology contract: what an operator must provision (shared PostgreSQL, leader election, bridge ring identity, shared encryption key) and how startup guardrails, settings concurrency control, and metrics semantics behave across replicas.
## Requirements
### Requirement: Multi-replica deployments require shared PostgreSQL coordination

Running more than one application replica SHALL require: a shared PostgreSQL database through which all cross-replica coordination flows (`scheduler_leader` lease, `bridge_ring_members`, `http_bridge_sessions`, `cache_invalidation`, `sticky_sessions`, `runtime_sentinels`); leader election enabled (`CODEX_LB_LEADER_ELECTION_ENABLED`, which defaults to `true`) so singleton schedulers run on exactly one replica; a unique instance id and a reachable replica-specific advertise URL per replica for bridge owner forwarding; and identical encryption key material mounted on every replica. Explicitly setting `CODEX_LB_LEADER_ELECTION_ENABLED=false` is the single-instance escape hatch that makes every replica treat itself as leader and MUST NOT be used with more than one replica.

#### Scenario: Supported two-replica topology

- **GIVEN** two replicas configured with the same PostgreSQL `CODEX_LB_DATABASE_URL`
- **AND** `CODEX_LB_LEADER_ELECTION_ENABLED` at its default (`true`) on both replicas
- **AND** each replica has a unique bridge instance id with a reachable replica-specific advertise URL
- **AND** both replicas mount the same encryption key file
- **WHEN** both replicas start
- **THEN** exactly one replica acquires the scheduler leader lease and runs singleton schedulers
- **AND** hard-continuity bridge requests landing on the non-owner replica are forwarded to the owner

#### Scenario: Leader election left at its default preserves the singleton guarantee

- **GIVEN** two replicas sharing one PostgreSQL database
- **AND** `CODEX_LB_LEADER_ELECTION_ENABLED` is left at its default (enabled)
- **WHEN** both replicas start
- **THEN** exactly one replica acquires the lease and runs singleton schedulers
- **AND** the operator observes no duplicate upstream polling (usage refresh, automations, retention)
- **AND** explicitly setting `CODEX_LB_LEADER_ELECTION_ENABLED=false` is the single-instance escape hatch that makes every replica treat itself as leader and run singleton schedulers N-fold

### Requirement: SQLite deployments are single-process

SQLite database backends SHALL be operated with exactly one application process; multi-process and multi-replica SQLite deployments — including `uvicorn --workers N` and sharing one SQLite file over a network volume — are unsupported. On SQLite the leader lease is NOT bypassed: it is arbitrated in the database through the same atomic conditional upsert used on PostgreSQL, so when more than one process is pointed at one SQLite file exactly one process wins the lease and the others observe rowcount 0 and remain followers. Multi-process SQLite remains unsupported for other reasons (single-writer contention), and the database rate limiter's cross-process atomicity is likewise unaffected.

#### Scenario: Two leader elections over one SQLite file arbitrate in the database

- **GIVEN** two `LeaderElection` instances pointed at the same SQLite database
- **WHEN** both attempt to acquire the leader lease while no unexpired lease exists
- **THEN** exactly one acquisition wins the lease
- **AND** the other observes rowcount 0 and remains a follower because SQLite arbitrates the lease in the database rather than bypassing it

#### Scenario: Operator scales a SQLite deployment

- **GIVEN** a deployment using a SQLite `CODEX_LB_DATABASE_URL`
- **WHEN** the operator wants more than one application process or replica
- **THEN** the supported path is migrating to a shared PostgreSQL database, not sharing the SQLite file

### Requirement: Startup verifies encryption-key consistency against the shared database

At startup, after schema readiness, each replica SHALL compute a fingerprint of its encryption key and atomically stamp it into `runtime_sentinels` (insert-if-absent), then compare its local fingerprint against the stored sentinel. When `CODEX_LB_ENCRYPTION_KEY_FINGERPRINT_MODE=enforce` (the default), a replica whose fingerprint differs from the stored sentinel SHALL refuse to start with an error naming both fingerprint prefixes and remediation steps; `warn` mode SHALL log an ERROR and continue; `off` SHALL disable the check.

#### Scenario: First boot stamps the sentinel

- **GIVEN** an empty `runtime_sentinels` table
- **WHEN** a replica starts
- **THEN** it stamps `sha256` of its encryption key as the `encryption_key_fingerprint` sentinel and starts normally

#### Scenario: Matching replica starts

- **GIVEN** a stamped `encryption_key_fingerprint` sentinel
- **WHEN** a second replica with the same encryption key starts
- **THEN** the fingerprint comparison passes and startup proceeds

#### Scenario: Divergent-key replica refuses to start in enforce mode

- **GIVEN** a stamped `encryption_key_fingerprint` sentinel
- **AND** `CODEX_LB_ENCRYPTION_KEY_FINGERPRINT_MODE` is `enforce`
- **WHEN** a replica with a different encryption key starts
- **THEN** startup fails with an error naming both fingerprint prefixes
- **AND** the error names the remediation (mount the shared key; after an intentional rotation, delete the sentinel row or set the mode to `warn`)

#### Scenario: Divergent-key replica continues in warn mode

- **GIVEN** a stamped `encryption_key_fingerprint` sentinel
- **AND** `CODEX_LB_ENCRYPTION_KEY_FINGERPRINT_MODE=warn`
- **WHEN** a replica with a different encryption key starts
- **THEN** an ERROR is logged and startup continues

#### Scenario: Concurrent first boot of two divergent replicas

- **GIVEN** an empty `runtime_sentinels` table
- **WHEN** two replicas with different encryption keys run the startup check concurrently
- **THEN** exactly one replica stamps the sentinel
- **AND** the other replica's comparison fails against the stamped value

### Requirement: Dashboard settings updates are optimistically locked

The dashboard settings row SHALL carry a monotonically increasing `version` incremented on every persisted ORM update, and full-row updates SHALL apply only when the version still matches the value read by the writer. The version check SHALL run for every accepted `PUT /api/settings`, including a save whose payload changes no field, so a stale writer cannot bypass the conflict guard by submitting an unchanged form. `GET`/`PUT /api/settings` responses SHALL expose `version`; the `PUT` payload MAY include `expectedVersion`, and a stale `expectedVersion` SHALL yield 409 before any write. Internal single-field writers (dashboard auth credential and TOTP mutations) SHALL retry on a version conflict rather than fail.

#### Scenario: Concurrent settings writers race

- **WHEN** two writers (any replicas or sessions) that read the same settings version race on `PUT /api/settings`
- **THEN** exactly one commit succeeds
- **AND** the loser receives 409 with code `settings_conflict` and no partial write

#### Scenario: Stale expectedVersion is rejected before any write

- **GIVEN** a `PUT /api/settings` payload carrying `expectedVersion` older than the current row version
- **WHEN** the update is submitted
- **THEN** the response is 409 with code `settings_conflict`
- **AND** no settings field is modified

#### Scenario: Writer committing between the version check and the update still loses

- **GIVEN** a `PUT /api/settings` request whose `expectedVersion` matched the row when the handler read it
- **WHEN** another writer commits a settings update before the first request's write is applied
- **THEN** the first request's write is rejected with 409 and code `settings_conflict`
- **AND** the interleaved writer's committed fields are not reverted

#### Scenario: Stale no-op save still enforces the version check

- **GIVEN** a `PUT /api/settings` whose payload assigns every field to the value the writer's own (stale) row already holds
- **WHEN** another writer commits a settings update before the no-op save is applied
- **THEN** the no-op save is rejected with 409 and code `settings_conflict`
- **AND** the interleaved writer's committed fields are not reverted

#### Scenario: Internal credential writer retries through a conflict

- **GIVEN** a dashboard-auth credential mutation whose session read the settings row before a concurrent settings update committed
- **WHEN** the credential mutation commits and hits a version conflict
- **THEN** it re-reads the fresh row, re-applies the mutation, and succeeds without surfacing an error

### Requirement: Metrics endpoint semantics are multi-process aware

WHEN metrics are enabled with `PROMETHEUS_MULTIPROC_DIR` set, the scrape registry SHALL aggregate counters across worker processes; WHEN metrics are enabled without `PROMETHEUS_MULTIPROC_DIR` and the standalone metrics port bind fails because another process already holds it, the losing process SHALL log an ERROR stating that `/metrics` reflects only one worker's counters and that `PROMETHEUS_MULTIPROC_DIR` is required for multi-worker aggregation. Multi-host deployments SHALL scrape each replica individually; counters are per-replica and scraping through a load-balanced VIP is unsupported.

#### Scenario: Multi-worker bind conflict without multiproc dir logs an ERROR

- **GIVEN** metrics are enabled and `PROMETHEUS_MULTIPROC_DIR` is not set
- **WHEN** a worker's standalone metrics server fails to bind because another process holds the port
- **THEN** the worker logs an ERROR naming the port and the `PROMETHEUS_MULTIPROC_DIR` remediation
- **AND** the worker keeps serving application traffic

#### Scenario: Multiproc mode keeps benign bind handling

- **GIVEN** metrics are enabled and `PROMETHEUS_MULTIPROC_DIR` is set
- **WHEN** a worker loses the metrics-port bind race
- **THEN** the worker logs at INFO level that another worker serves metrics

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

