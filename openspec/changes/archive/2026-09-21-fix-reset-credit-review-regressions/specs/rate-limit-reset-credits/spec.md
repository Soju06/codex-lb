## ADDED Requirements

### Requirement: Reset scheduler survives consume session cleanup
The scheduler SHALL retain usable queued work after the originating database session closes and SHALL continue processing other accounts after a confirmed consume.

#### Scenario: Confirmed consume followed by queued accounts
- **WHEN** a real automatic consume confirms a reset and its session is closed
- **THEN** quota verification and remaining deadline work SHALL continue without accessing expired ORM attributes

### Requirement: Received reset receipts survive transient settlement failure
The system SHALL use bounded cancellation-safe retries to persist a received consume receipt without repeating the upstream consume.

#### Scenario: Transient write failure after confirmation
- **WHEN** the first receipt write fails but a subsequent bounded write succeeds
- **THEN** the confirmed receipt SHALL be durable and usage verification SHALL remain recoverable with exactly one upstream consume

#### Scenario: Cancellation during settlement
- **WHEN** the caller is cancelled after receiving a consume result
- **THEN** owned settlement SHALL finish before cancellation propagates and SHALL leave no detached settlement task

### Requirement: Manual consumes reconcile credit identity
Under the account serializer, manual consumes SHALL consult durable outcomes for the selected credit before creating a new consume request.

#### Scenario: New request ID with stale available credit
- **WHEN** a new manual request selects a credit with a terminal durable outcome
- **THEN** it SHALL NOT send another upstream consume for that credit

#### Scenario: Legacy caller retries without a request body
- **WHEN** a no-body manual consume fails transiently and the same selected credit remains available
- **THEN** a no-body retry SHALL reuse that credit's existing unresolved request ID
- **AND** SHALL NOT create a second upstream consume identity for that credit

#### Scenario: Browser reload creates a new client request ID
- **WHEN** a new manual client ID selects a credit with an unresolved durable attempt
- **THEN** it SHALL be pinned to that credit and reuse the original upstream request identity
- **AND** later retries of either ID SHALL resolve that original receipt rather than consume a different credit

#### Scenario: Alias retention and replica clock skew
- **WHEN** an alias remains within its retention window while the canonical pin is older, or the alias was created on a slower replica clock
- **THEN** the canonical receipt and request identity SHALL remain available for alias replay
- **AND** purge SHALL retain the credit's pins while any pin remains within the retention window
- **AND** alias timestamps SHALL NOT make an alias the upstream request owner

### Requirement: Targeted reset summaries preserve authoritative metadata
Snapshot reconciliation SHALL NOT increase the upstream availability count. Targeted account summaries SHALL preserve the duplicate-identity classification of the full account list.

#### Scenario: Zero count with stale available items
- **WHEN** the post-consume snapshot reports zero available credits alongside stale available items
- **THEN** reconciliation SHALL retain zero available credits

#### Scenario: Targeted summary for duplicate identity
- **WHEN** an account is classified as a duplicate in the full list
- **THEN** its targeted summary SHALL retain that classification without loading the full account list

### Requirement: Automatic retry respects remaining lifetime
Automatic retry scheduling SHALL use the persisted retry time and remaining lifetime of the original credit, and SHALL NOT defer every retry past its expiry when time remains for another bounded attempt.

#### Scenario: Transient failure twenty seconds before expiry
- **WHEN** a fast transient attempt fails with twenty seconds left
- **THEN** a retry SHALL be scheduled before expiry using the same credit and request identity
- **AND** attempts SHALL cease after expiry without a rapid retry loop

## MODIFIED Requirements

### Requirement: Reset credit redemption is serialized and idempotent across replicas

Per-account redemption serialization MUST hold across all replicas and processes sharing one database. On PostgreSQL the system SHALL use `pg_advisory_xact_lock` keyed by the account id on the caller's session. On SQLite the system SHALL acquire a durable claim row via a single atomic conditional upsert (`INSERT ... ON CONFLICT(account_id) DO UPDATE ... WHERE expires_at < now`) with a 30-second lease, a bounded retry loop that surfaces a client-facing conflict on timeout, release on completion, and takeover of expired claims. While the redeem section runs, the claim holder SHALL renew its lease on a heartbeat cadence shorter than the lease (10 seconds) so a redemption that legitimately outlives one lease (e.g. slow upstream fetch/consume) is NOT taken over by a concurrent process; lease expiry without renewal remains the crash-recovery path. A claim-acquisition timeout SHALL surface in the caller surface's native error envelope: the dashboard error envelope on the dashboard consume endpoint and the `/v1/*` OpenAI error envelope (HTTP 409) on `POST /v1/reset-credit`. The system SHALL persist the `(account_id, redeem_request_id) -> credit_id` mapping in the shared database, committed inside the serialized section BEFORE the upstream consume call; a retry carrying the same `redeem_request_id`, served by ANY replica, MUST resolve to the originally selected `credit_id` and MUST NOT consume a different credit. Ledger rows SHALL be retained at least 24 hours, including after a failed consume. Retention SHALL apply to the account/credit group: while any pin in the group has a `created_at` within 24 hours, all its pins and canonical outcome SHALL remain available. A group with no pin inside that window SHALL be treated as absent on read and purged opportunistically BEFORE a new pin is inserted. Reusing a `redeem_request_id` after its entire prior credit group expires SHALL durably re-pin the newly selected credit instead of colliding with an expired row. Read eligibility and purge eligibility SHALL use the same group retention rule. Both the dashboard consume endpoint and `POST /v1/reset-credit` SHALL redeem inside this cross-replica serialized section. Confirmed outcome receipts SHALL short-circuit repeat consumption. Automatic recovery SHALL additionally enforce the exact-credit availability and expiry checks even when a durable pin exists. Legacy pins SHALL be classified as unknown until reconciled and SHALL NOT create an additional automatic request for the same credit during an upgrade.

#### Scenario: Retry lands on a second replica and reuses the pinned credit
- **GIVEN** replica A redeemed the soonest credit for `redeem_request_id` R but the client never saw the response
- **WHEN** the client retries the consume with the same R and the request is served by replica B
- **THEN** replica B returns the persisted confirmed receipt when present, or replays the originally pinned `credit_id` with the same request id when the result remains unresolved
- **AND** no second credit is consumed for that account

#### Scenario: Two processes on one SQLite file redeem concurrently
- **GIVEN** two processes sharing one SQLite database each receive a consume request for the same account at nearly the same time
- **WHEN** the first process holds the durable redeem claim
- **THEN** the second process waits on (or conflicts out of) the claim instead of redeeming in parallel
- **AND** at most one upstream consume is sent per selected credit

#### Scenario: Claim holder crashes and the lease recovers
- **GIVEN** a process crashed while holding the redeem claim for an account
- **WHEN** a later consume request arrives after the claim lease has expired
- **THEN** the request takes over the expired claim and proceeds without operator intervention

#### Scenario: Slow redemption keeps its claim past the original lease
- **GIVEN** a process holds the redeem claim and its redeem section (upstream fetch/consume, usage refresh) runs longer than one 30-second lease
- **WHEN** a second process attempts to acquire the claim after the original lease would have expired
- **THEN** the heartbeat-renewed lease rejects the takeover and the second process keeps waiting (or conflicts out)
- **AND** at most one upstream consume is sent per selected credit

#### Scenario: Reused redeem_request_id after TTL re-pins the new credit
- **GIVEN** an account has a ledger row for `redeem_request_id` R pinned to credit C1 whose entire account/credit group has aged past the 24h TTL
- **WHEN** a new redemption reuses R and selects a different credit C2
- **THEN** the expired row is purged before the new insert so the ledger persists `(R -> C2)`
- **AND** a same-R retry served by any replica retargets C2, not the discarded C1

#### Scenario: Expired pin is ignored on read
- **GIVEN** an account has a ledger row for `redeem_request_id` R whose entire account/credit group has aged past the 24h TTL
- **WHEN** the pin lookup for `(account_id, R)` runs before any purge write
- **THEN** the lookup returns no durable pin (the expired row reads as absent)
- **AND** the redemption re-selects against the fresh fetch and re-pins the newly selected credit rather than forwarding the stale expired `credit_id`

#### Scenario: Claim contention on the v1 surface uses the OpenAI envelope
- **GIVEN** another process holds the redeem claim for the whole acquisition timeout
- **WHEN** a client calls `POST /v1/reset-credit` for that account
- **THEN** the endpoint returns 409 in the `/v1/*` OpenAI error envelope, not the dashboard envelope
