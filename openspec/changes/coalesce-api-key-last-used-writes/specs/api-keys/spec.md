## ADDED Requirements

### Requirement: API key last-used tracking is write-behind and coalesced

The system SHALL track `api_keys.last_used_at` through a process-local write-behind coalescer instead of writing the column inside each reservation-settlement transaction. Settlement paths MUST record the key's used-at timestamp in memory (keyed by API key id, keeping the per-key maximum), and a replica-local periodic flusher (constant 30-second interval, not leader-gated) MUST fold all pending touches into the database in a single transaction per flush. Every flushed write MUST apply monotonic greatest-wins semantics — the stored `last_used_at` is only advanced, never regressed, even when multiple replicas flush out of order (`GREATEST(coalesce(last_used_at, epoch), :new)` semantics; the dialect-portable guarded UPDATE `WHERE last_used_at IS NULL OR last_used_at < :new` is an acceptable implementation on both PostgreSQL and SQLite). Graceful shutdown MUST flush every recorded touch: the flusher's stop sequence MUST switch the coalescer to shutdown write-through mode before performing the final flush, so a touch recorded after (or concurrently with) the final flush — for example by a settlement task that outlived the shutdown drain of persistence tasks — is flushed immediately by the recording path itself instead of being parked in a pending map that no longer has a flusher. Shutdown-path flushes (the final flush and write-through flushes after it) MUST retry transient failures a bounded number of times (3 attempts with a short constant backoff); if every attempt fails, the pending touches (API key ids and their timestamps) MUST be logged at WARNING so operators can reconstruct the lost values, and the failure MUST NOT propagate to the caller. On process crash, losing at most one flush interval (~30 seconds) of `last_used_at` freshness is accepted: the column's only consumer is the dashboard API response field (`lastUsedAt`), which no routing, ordering, or enforcement logic reads, so observed staleness of up to the flush interval is a display-only effect. A failed periodic flush MUST retain the pending touches for a later flush rather than dropping them.

#### Scenario: Many settlements within one interval flush as one write per key

- **GIVEN** an API key that settles many requests within one flush interval
- **WHEN** the periodic flush runs
- **THEN** the key receives exactly one `last_used_at` write carrying the latest recorded used-at timestamp
- **AND** none of the individual settlement transactions wrote `last_used_at`

#### Scenario: Flush never moves last_used_at backwards

- **GIVEN** a stored `last_used_at` newer than a pending recorded timestamp (for example another replica already flushed a later touch)
- **WHEN** the flush applies the pending timestamp
- **THEN** the stored `last_used_at` keeps the newer value

#### Scenario: Graceful shutdown flushes pending touches

- **GIVEN** recorded touches that have not yet been flushed
- **WHEN** the application shuts down gracefully
- **THEN** the pending touches are flushed to the database before the process exits

#### Scenario: Failed flush retains pending touches

- **GIVEN** a flush attempt that fails (for example a transient database error)
- **WHEN** the next flush tick runs
- **THEN** the previously pending touches are flushed, merged with any touches recorded in between (per-key maximum wins)

#### Scenario: Shutdown final flush retries a transient failure

- **GIVEN** pending touches and a database that fails the first final-flush attempt with a transient error
- **WHEN** the application shuts down gracefully
- **THEN** the final flush is retried after a short backoff and the touches are persisted before the process exits

#### Scenario: Shutdown final flush exhausts its retries

- **GIVEN** pending touches and a database that fails every final-flush attempt
- **WHEN** the bounded retries are exhausted
- **THEN** a WARNING is logged containing the pending API key ids and their timestamps
- **AND** shutdown proceeds without raising

#### Scenario: Touch recorded after the shutdown flush writes through

- **GIVEN** a settlement task that outlived the shutdown drain of persistence tasks
- **WHEN** it records a touch after the flusher has stopped and performed its final flush
- **THEN** the touch is flushed to the database immediately by the recording path rather than being lost at process exit
