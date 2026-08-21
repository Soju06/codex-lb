## ADDED Requirements

### Requirement: Keyed stream mid-loop failover settles before account-health writes

When an HTTP SSE Responses stream holds an API-key usage reservation, mid-loop failover account-health writes for a failed account MUST NOT run while that reservation remains unsettled. The stream MUST keep the same reservation across the internal failover, MUST defer the failed account's health write until settlement is confirmed, and MUST NOT acquire a second reservation solely for that failover. If primary settlement fails but fail-safe release confirms, the stream MAY flush deferred health after that confirmed release when ordered settle never ran. If neither settlement nor fail-safe release confirms, deferred health MUST stay unapplied. After settlement commits, the stream MUST record that settled state before awaiting deferred health flush so a cancellation that arrives during the flush cannot skip retained deferred penalties. Deferred health flush MUST consume one queued entry at a time and MUST retain later entries when one write fails or cancellation interrupts an await. Deferred health flush MUST complete each queued entry under cancellation-deferred ownership so a cancel mid-write cannot replay the same health operation and double-count errors. After cancellation, cleanup MUST attempt to settle or release the reservation. Cleanup MUST flush deferred health before it finishes only after settlement or release is confirmed. If neither operation confirms, deferred health MUST remain unapplied.

#### Scenario: Keyed refresh/connect failover defers health until settle

- **GIVEN** a keyed HTTP SSE Responses stream with a held API-key reservation
- **AND** the first account fails a retryable freshness/connect transport error
- **WHEN** a later account completes and settlement runs
- **THEN** `_handle_stream_error` for the failed account runs only after that settlement
- **AND** the request does not acquire another reservation

#### Scenario: Keyed transient exhaustion defers health until settle

- **GIVEN** a keyed HTTP SSE Responses stream with a held API-key reservation
- **AND** the first account exhausts same-account transient stream retries
- **WHEN** a later account completes and settlement runs
- **THEN** `_handle_stream_error` and extra `record_errors` for the failed account run only after that settlement

#### Scenario: Streaming Responses route preserves settle-before-health

- **GIVEN** a keyed request admitted through the streaming `/v1/responses` entry point
- **AND** mid-loop keyed failover queues a deferred account-health penalty
- **WHEN** the replacement account completes
- **THEN** reservation settlement commits before the deferred health write

#### Scenario: Cancel after queued mid-loop penalty still flushes health

- **GIVEN** a keyed stream that queued a deferred mid-loop health penalty
- **WHEN** the request is cancelled before the replacement settles
- **AND** cleanup confirms settlement or fail-safe release
- **THEN** the deferred health write still runs

#### Scenario: Cancel during deferred health flush still applies the penalty

- **GIVEN** a keyed stream whose settlement already committed
- **AND** deferred health flush is awaiting an account-health write
- **WHEN** the request is cancelled during that await
- **THEN** the deferred health write still completes for the failed account

#### Scenario: Cancel mid deferred health write does not double-count

- **GIVEN** a keyed stream whose settlement already committed
- **AND** deferred health flush has applied in-memory health for a queued entry
- **AND** the flush is still awaiting persistence or extra `record_errors`
- **WHEN** the request is cancelled during that await
- **THEN** cleanup MUST NOT replay the same queued entry
- **AND** `_handle_stream_error` and extra `record_errors` for that entry apply exactly once

#### Scenario: Deferred health flush keeps later entries after one failure

- **GIVEN** a keyed stream that deferred health for more than one failed account
- **WHEN** the first deferred health write raises
- **THEN** later deferred health writes are still attempted

#### Scenario: Unconfirmed settlement keeps deferred health unapplied

- **GIVEN** a keyed stream that deferred a mid-loop health penalty
- **WHEN** neither primary settlement nor fail-safe release confirms settlement
- **THEN** the deferred health write does not run
