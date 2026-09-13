# live-usage-ingestion Specification

## Purpose
Governs the passive usage source that reads rate-limit headers and `codex.rate_limits` stream events from proxied traffic. Polling upstream usage alone leaves selection state up to a refresh interval behind real usage, so bursty traffic could exhaust a window before the poller noticed. This capability turns every proxied turn into a usage snapshot while guaranteeing that ingestion never impairs the serving path, is throttled per account, can be switched off, and has an instance-scoped lifecycle.
## Requirements
### Requirement: Proxied responses feed passive usage snapshots

The proxy SHALL parse upstream rate-limit signals from proxied traffic — `x-codex-{primary,secondary}-used-percent`, `-window-minutes`, `-reset-at`, and `x-codex-credits-*` response headers, and `codex.rate_limits` stream events — into per-account usage snapshots attributed to the account that served the request. Snapshots SHALL be persisted through the same usage-history storage contract the background poller uses (per-window rows with used percent, reset timestamp, window duration, and credits fields).

#### Scenario: Stream event updates usage rows

- **WHEN** a proxied response stream carries a `codex.rate_limits` event for an account
- **THEN** the account's primary and secondary usage rows reflect the event's used percentages, reset timestamps, and window durations without waiting for the next poll

#### Scenario: Response headers update usage rows

- **WHEN** an upstream response carries `x-codex-*` rate-limit headers
- **THEN** an equivalent snapshot is ingested for the serving account

#### Scenario: Snapshots without any window are ignored

- **WHEN** a response carries no rate-limit headers and no rate-limit stream event
- **THEN** nothing is ingested

### Requirement: Live ingestion never impairs the serving path

Live usage ingestion MUST be fire-and-forget: parsing failures, storage failures, and backpressure MUST NOT fail, block, or slow the proxied request beyond enqueueing a snapshot. When the ingest queue is full, the oldest snapshot SHALL be dropped and the drop counted in logs.

#### Scenario: Storage failure does not affect the stream

- **WHEN** persisting a live snapshot fails
- **THEN** the proxied response continues unaffected
- **AND** the failure is logged with the account id

#### Scenario: Queue overflow drops oldest

- **WHEN** the ingest queue is at capacity
- **THEN** the oldest queued snapshot is dropped in favor of the newest

### Requirement: Live writes are throttled per account

Live snapshot writes SHALL be throttled per account by a change fingerprint and a minimum write interval: a snapshot identical to the last persisted one is skipped, and unchanged-window writes within the interval are coalesced. A changed snapshot MAY be written immediately.

#### Scenario: Duplicate snapshots are coalesced

- **WHEN** consecutive turns observe identical rate-limit values within the minimum write interval
- **THEN** at most one usage row set is written

#### Scenario: Changed usage writes promptly

- **WHEN** a snapshot's used percentage or reset timestamp differs from the last persisted values
- **THEN** the write is not deferred by the unchanged-write interval

### Requirement: Captured live snapshots survive account consolidation

The proxy MUST enqueue both the serving local account id and the upstream
ChatGPT account id when both are available. At consumption, live usage
ingestion MUST prefer the captured local id when it still identifies an
account. If that local id is absent or no longer exists, ingestion MUST use the
captured upstream id only when it resolves to exactly one current local account.
For a selected owner, ingestion MUST atomically persist no more than one history
row for each window represented by the queued snapshot. On PostgreSQL,
ingestion MUST acquire a transaction-scoped advisory lock keyed by the captured
upstream identity before either owner lookup and hold it through snapshot
commit. If the selected local owner's current non-null upstream identity is not
already locked, ingestion MUST roll back the initial transaction, reacquire the
captured and current identity locks in canonical sorted order, and reselect and
revalidate the owner before persistence. If that local owner was consolidated
while the current-identity lock was acquired, ingestion MUST use the last
observed current identity only when it resolves to exactly one surviving local
account. Ingestion MUST perform at most one such relock and MUST raise a typed
error if the selected owner's identity changes again; a null current identity
MUST NOT create an advisory-lock key. Every account writer that can change
membership in an upstream identity MUST acquire the same lock before row locks
or mutation and hold it through commit. Writers moving membership between two
non-null upstream identities MUST acquire both stable lock keys in canonical
sorted order.

#### Scenario: Stale duplicate settles under the unique canonical account

- **GIVEN** a primary/secondary live snapshot was queued for duplicate account `D`
- **AND** the queued item contains `D` and the upstream identity shared with canonical account `C`
- **AND** duplicate reconciliation reparents existing history to `C` and deletes `D`
- **WHEN** the queued snapshot is consumed
- **THEN** exactly one primary row and one secondary row are persisted under `C`
- **AND** no usage-history row is persisted under `D`
- **AND** the persisted values equal the captured snapshot

#### Scenario: A valid local owner takes precedence

- **GIVEN** a queued snapshot contains a local account id that still exists
- **AND** it also contains an upstream identity usable for fallback
- **WHEN** the queued snapshot is consumed
- **THEN** the snapshot is persisted under the captured local account
- **AND** ingestion does not substitute another account selected by the upstream identity

#### Scenario: A selected owner's current identity is revalidated

- **GIVEN** a queued snapshot contains local account `A` and captured identity `X`
- **AND** `A` currently belongs to identity `Y`
- **WHEN** settlement overlaps reconciliation of `A` into a canonical `Y` owner
- **THEN** settlement releases its initial `X` lock before acquiring the canonical sorted lock set for `X` and `Y`
- **AND** settlement reselects and revalidates the owner under that full lock set
- **AND** exactly one row per represented window survives under the canonical `Y` owner
- **AND** a second selected-owner identity change raises a typed terminal error without persisting the snapshot

#### Scenario: Upstream-only publication still resolves

- **GIVEN** a queued snapshot has no local account id
- **AND** its upstream identity resolves to exactly one current local account
- **WHEN** the queued snapshot is consumed
- **THEN** the snapshot is persisted once under that local account

#### Scenario: Consolidation cannot delete a snapshot inserted after reparenting

- **GIVEN** PostgreSQL settlement has selected duplicate `D` for a captured upstream identity
- **AND** reconciliation would reparent `D` history to `C` and then delete `D`
- **WHEN** settlement and reconciliation overlap across independent sessions
- **THEN** their shared transaction-scoped upstream-identity lock serializes the complete membership change
- **AND** the snapshot is either committed under `D` before reparenting or directly under `C` after reconciliation
- **AND** exactly one row per represented window survives under `C`

#### Scenario: Ambiguous fallback does not guess an owner

- **GIVEN** the captured local account id is absent or no longer exists
- **AND** the captured upstream identity matches multiple current local accounts
- **WHEN** the queued snapshot is consumed
- **THEN** no usage-history row is persisted for that snapshot

### Requirement: Ingestor-owned task failures are settled at completion

Every background task the live usage ingestor creates MUST be settled when it
completes: if the task ends with an exception other than cancellation, the
exception MUST be retrieved at completion time, logged immediately with its
traceback, and recorded in a bounded in-process failure record as
traceback-free metadata (task name and exception representation) so the
record cannot retain the failed task's object graph. An
ingestor-owned task failure MUST NOT surface as a garbage-collection-time
unobserved-task warning. Each task MUST be settled exactly once, including
when an external supervisor (such as test infrastructure) also observes the
task. Settlement MUST NOT extend task lifetime, change ingestion behavior, or
affect the serving path.

#### Scenario: Detached consumer death is logged deterministically

- **GIVEN** a consumer task whose owner lost track of it (for example a stop
  cancelled between clearing the singleton and awaiting the task)
- **WHEN** the task dies with an exception
- **THEN** the exception is retrieved and logged at completion time
- **AND** it is recorded in the bounded failure record
- **AND** no unobserved-task warning fires at garbage collection

#### Scenario: Cancelled tasks settle silently

- **WHEN** an ingestor-owned task ends by cancellation
- **THEN** settlement records no failure and logs no error

#### Scenario: Failure record stays bounded

- **WHEN** ingestor-owned tasks fail repeatedly without the record being
  drained
- **THEN** the failure record retains at most its fixed capacity of entries
- **AND** every failure is still logged

### Requirement: Ingestor lifecycle is instance-scoped

Each application lifespan MUST hold the ingestor instance its startup created
and stop exactly that instance at shutdown. Stopping an instance MUST touch
the process-wide singleton registration and the publisher hook only when the
stopped instance still owns them; when it does own them, the most recently
displaced ingestor that is still running MUST be restored as the registration
and publisher. Restoration eligibility is defined as: the candidate holds an
existing consumer task whose `done()` is false — this excludes consumers that
failed or completed, and ingestors whose `stop()` already cleared their
consumer. An instance MUST be removed from restoration tracking before its own
shutdown begins, so a stopping or stopped instance can never be restored
later. When several
lifespans are live in one process, no lifespan's startup or shutdown may
orphan another lifespan's running ingestor, leave it without a stop path, or
leave it registered-less while it still runs.

#### Scenario: Nested lifespan cannot orphan the outer ingestor

- **GIVEN** an app whose lifespan started ingestor A
- **WHEN** a nested lifespan starts ingestor B (taking over the singleton and
  publisher) and later stops it
- **THEN** ingestor A keeps running, strongly rooted by its own lifespan
- **AND** the outer lifespan's shutdown stops ingestor A and its tasks

#### Scenario: Nested shutdown restores the outer registration

- **GIVEN** an app whose lifespan started ingestor A
- **AND** a nested lifespan whose startup displaced A by registering
  ingestor B
- **WHEN** the nested lifespan stops ingestor B
- **THEN** ingestor A is restored as the singleton registration and publisher
- **AND** publications after the nested exit flow to ingestor A and are
  ingested
- **AND** a displaced ingestor that already stopped is not restored (the
  registration falls through to the next still-running displaced instance,
  or is cleared)

#### Scenario: A failed displaced ingestor is not restored

- **GIVEN** displaced ingestor A whose consumer task has settled with an
  exception (its task `done()` is true)
- **WHEN** the current registration stops
- **THEN** A is skipped by restoration (fall through to the next eligible
  displaced instance, or clear the registration)

#### Scenario: A stopping displaced ingestor is not restored

- **GIVEN** displaced ingestor A whose `stop()` has begun (A was removed from
  restoration tracking before its shutdown started)
- **WHEN** the current registration stops concurrently
- **THEN** A is never restored, even if its consumer task has not yet finished

### Requirement: Live ingestion is decoupled and always on

The core client layer SHALL publish snapshots through a hub that no-ops until the module layer registers an ingestor at startup. The module layer SHALL register the ingestor unconditionally at startup; there is no operator switch for live ingestion, and `CODEX_LB_LIVE_USAGE_INGESTION_ENABLED` is a removed setting that startup reports and ignores.

#### Scenario: Removed kill switch is ignored

- **WHEN** the process starts with `CODEX_LB_LIVE_USAGE_INGESTION_ENABLED=false`
- **THEN** the ingestor is still registered and proxied responses produce usage writes
- **AND** startup logs the removed-setting warning once

#### Scenario: Unregistered hub is inert

- **WHEN** snapshots are published before an ingestor is registered
- **THEN** they are discarded without error

