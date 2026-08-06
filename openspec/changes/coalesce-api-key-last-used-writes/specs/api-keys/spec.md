## ADDED Requirements

### Requirement: API key last-used tracking is write-behind and coalesced

The system SHALL track `api_keys.last_used_at` through a process-local write-behind coalescer instead of writing the column inside each reservation-settlement transaction. Settlement paths MUST record the key's used-at timestamp in memory (keyed by API key id, keeping the per-key maximum), and a replica-local periodic flusher (constant 30-second interval, not leader-gated) MUST fold all pending touches into the database in a single transaction per flush. Every flushed write MUST apply monotonic greatest-wins semantics — the stored `last_used_at` is only advanced, never regressed, even when multiple replicas flush out of order (`GREATEST(coalesce(last_used_at, epoch), :new)` semantics; the dialect-portable guarded UPDATE `WHERE last_used_at IS NULL OR last_used_at < :new` is an acceptable implementation on both PostgreSQL and SQLite). Graceful shutdown MUST perform a final flush after proxy settlement tasks drain. On process crash, losing at most one flush interval (~30 seconds) of `last_used_at` freshness is accepted: the column's only consumer is the dashboard API response field (`lastUsedAt`), which no routing, ordering, or enforcement logic reads, so observed staleness of up to the flush interval is a display-only effect. A failed flush MUST retain the pending touches for a later flush rather than dropping them.

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
