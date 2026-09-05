## ADDED Requirements

### Requirement: Retry-circuit admission uses an immutable generation fence

For a hard-affinity HTTP bridge key, a verified stale-anchor replay MUST capture
the durable retry-circuit state and local admission state in one typed immutable
snapshot. Immediately before queue publication, the replay MUST atomically
claim that snapshot by advancing only `admission_generation` and recording a
nullable claim start epoch, claimed generation, and claim-until epoch; it MUST compare
the captured durable timestamp, failure count, cooldown, and generation, and it
MUST compare the captured local failure/cooldown state before and after the
durable operation. The claim MUST use a dialect-guarded SQLite/PostgreSQL
`RETURNING` statement so a successful claim receipt is part of the same write.
The claim lease MUST cover the request's remaining budget plus 60 seconds of
cleanup grace (or two hours plus that grace when no request deadline is
provided), and an active lease MUST block a second claim. The caller MUST pass
the lease as a relative duration. The durable repository MUST derive the stored
claim-until epoch and every active/expired receipt comparison from the database
clock; caller wall-clock values MUST NOT decide lease liveness.

#### Scenario: A newer same-key local failure suppresses a delayed claim

- **GIVEN** a stale-anchor replay captured a hard-key generation
- **WHEN** a local failure advances that key before the durable claim returns
- **THEN** the post-claim local check MUST reject the replay
- **AND** the failure's local admission state MUST remain installed

#### Scenario: A competing durable claim wins first

- **GIVEN** two replicas captured the same hard-key generation
- **WHEN** one replica advances `admission_generation`
- **THEN** the other replica's conditional claim MUST return no receipt
- **AND** it MUST fail closed without dispatching a second replay

#### Scenario: Replica clock skew cannot steal a live claim

- **GIVEN** one replica owns a claim that remains live on the database clock
- **WHEN** another replica's wall clock is ahead of the claim expiry
- **THEN** the second replica MUST NOT advance the admission generation
- **AND** reset, purge, and guarded migration downgrade MUST preserve the live receipt

#### Scenario: A timed-out claim is reconciled within the request budget

- **GIVEN** the first durable claim attempt times out
- **WHEN** the request still has budget remaining and the first operation has
  settled cancellation
- **THEN** the service MAY retry the identical conditional claim once
- **AND** a committed first claim MUST make that retry refuse through the generation fence
- **AND** a second timeout, store error, refusal, or expired deadline MUST remain fail-closed

#### Scenario: A cancellation-resistant claim cannot extend the request budget

- **GIVEN** a durable claim ignores cancellation after its time bound
- **WHEN** the claim timeout elapses
- **THEN** the request MUST stop waiting at that bound
- **AND** it MUST NOT issue a concurrent reconciliation write
- **AND** the replay MUST remain fail-closed even if the detached operation later commits

### Requirement: Claim-receipt migration rollback is guarded

The migration that adds `admission_claimed_at_epoch`,
`admission_claimed_generation`, and `admission_claimed_until_epoch` MUST keep
the columns nullable. Its downgrade MUST inspect the durable claim-until
epochs against the database clock before changing the schema or Alembic
version: any unexpired receipt
MUST make the downgrade fail before DDL or version stamping. When no live
receipt remains (all receipt epochs are null or expired), the downgrade MUST
remove the marker columns so the parent revision remains compatible with its
ORM model and startup schema-drift check.
The receipt inspection and column removal MUST be serialized in one critical
section: PostgreSQL MUST hold an `ACCESS EXCLUSIVE` lock on the retry-circuit
table, and SQLite MUST acquire `BEGIN IMMEDIATE` before inspecting receipts.
This serialization MUST cover direct Alembic downgrade invocations that bypass
the application migration mutex.

#### Scenario: An active receipt blocks migration rollback

- **GIVEN** the marker migration is applied and a retry row has a claim-until
  epoch later than the current epoch
- **WHEN** an operator downgrades to the parent revision
- **THEN** the downgrade MUST refuse before dropping any marker column
- **AND** the receipt row and marker migration version MUST remain intact

#### Scenario: A released or expired receipt permits migration rollback

- **GIVEN** the marker migration is applied and no retry row has an unexpired
  claim receipt
- **WHEN** an operator downgrades to the parent revision
- **THEN** the downgrade MUST remove the marker columns and advance the version
  to the parent
- **AND** re-upgrading the marker migration MUST restore nullable columns with
  no invented receipt

#### Scenario: A concurrent claim cannot race migration rollback

- **GIVEN** the marker migration is applied and no retry row has an unexpired
  claim receipt
- **WHEN** a durable claim races the downgrade's receipt check and marker drop
- **THEN** the downgrade's table or writer lock MUST serialize the claim
- **AND** a claim committed before the check MUST make the downgrade refuse
- **AND** a claim started after the lock MUST NOT be silently dropped by the
  marker-column removal

### Requirement: Retry-circuit settlement is generation-fenced

When a hard-key retry circuit is cleared, the service MUST retain local
admission state if durable lookup fails. A present durable row MUST be cleared
only when both its observed `updated_at_epoch` and `admission_generation` still
match. A conditional-clear refusal MUST report no match and MUST NOT remove
local state. A confirmed durable miss MAY remove a local marker only when no
newer local failure arrived during the lookup. Delayed failure persistence MUST
merge using the existing failure observation metadata without rewriting the
independent `admission_generation` or an active claim receipt. A terminal,
aborted, cancelled, or proven pre-dispatch-cleaned replay MUST release its
receipt by matching the claimed generation and any captured timestamps; an
older receipt MUST NOT clear a reclaimed marker. A durable release refusal or
exception MUST NOT escape terminal cleanup or discard the receipt, and MUST
schedule the existing service-owned generation-fenced release retry.
When terminal stale-anchor recovery transfers a receipt to a same-owner retry,
the retry MUST fence pre-dispatch cleanup against its own current
`response_create_attempt_count`; the source request's historical attempt count
MUST NOT be reused after transfer.

#### Scenario: A newer durable failure survives an older success

- **GIVEN** a response captured generation `g`
- **WHEN** another writer records a failure and advances the row before the
  response clears it
- **THEN** the generation-fenced clear MUST return no match
- **AND** the newer durable failure and local admission guard MUST remain

#### Scenario: Durable lookup outage does not erase local protection

- **GIVEN** a local hard-key circuit is installed
- **WHEN** durable lookup raises during successful-response settlement
- **THEN** settlement MUST leave the local circuit and marker sets intact
- **AND** the request MUST not claim that the circuit was cleared

#### Scenario: Same-key success preserves an active replay receipt

- **GIVEN** an authorized stale-anchor replay owns an active claim receipt
- **WHEN** another same-key request completes successfully
- **THEN** the retry-circuit failure count, cooldown, and detail MUST reset
- **AND** the active claim generation and receipt MUST remain unchanged
- **AND** the replay owner MUST still release that receipt explicitly at
  terminal settlement

#### Scenario: Durable receipt release fails during terminal cleanup

- **GIVEN** an HTTP or WebSocket replay reaches terminal or aborted cleanup
  while it still owns an active claim receipt
- **WHEN** the bounded durable release returns no match or raises
- **THEN** terminal cleanup MUST continue without propagating that store failure
- **AND** the request MUST retain its claim receipt
- **AND** the service MUST schedule a generation-fenced release retry

#### Scenario: A transferred receipt uses the retry's attempt baseline

- **GIVEN** a stale-anchor source request owns a receipt after one or more
  response-create attempts
- **WHEN** recovery transfers the receipt to a freshly prepared same-owner retry
- **THEN** the source request MUST be detached from the receipt before reset
- **AND** the retry MUST store its own current `response_create_attempt_count`
  as the pre-dispatch cleanup fence
- **AND** a retry setup failure before its first send MUST release the receipt
- **AND** an ambiguous retry send that increments its attempt count MUST retain
  the receipt for reconciliation

### Requirement: Retry-circuit stale purges are generation-fenced

Expired retry-circuit purges MUST compare the captured `updated_at_epoch` and
`admission_generation` in their delete predicate and MUST exclude rows with an
unexpired claim receipt. A purge that loses a generation or claim-receipt race
MUST leave the newer row intact. An expired claim MAY be reclaimed only by a
new generation-fenced claim. If a stale-row purge returns no match or raises
before confirming deletion, the loader MUST immediately report the durable
state as uncertain for that call. A later reload MUST NOT clear that
uncertainty, including when it finds a fresh below-threshold row.

#### Scenario: A claim survives a stale purge

- **GIVEN** a cleanup read captured an expired retry row at generation `g`
- **WHEN** a replay claim advances that row to generation `g + 1` before cleanup deletes it
- **THEN** the cleanup delete MUST match no row
- **AND** the claimed row MUST remain available for later generation-fenced settlement

#### Scenario: Active and expired claim leases

- **GIVEN** a retry row has an unexpired claim receipt
- **WHEN** per-key or batch cleanup runs
- **THEN** cleanup MUST leave that row intact
- **WHEN** the receipt expires and a replay captures the current generation
- **THEN** the replay MAY reclaim the row by advancing the generation and
  recording a new lease
- **AND** a release using the prior generation MUST return no match

#### Scenario: An uncertain stale purge suppresses pre-created admission

- **GIVEN** a loader observed an expired retry row for a hard-affinity key
- **WHEN** its conditional purge returns no match or cannot complete
- **THEN** pre-created retry admission MUST fail closed for that call
- **AND** its cooldown hint MUST remain fail closed instead of using an
  untrusted local fallback
