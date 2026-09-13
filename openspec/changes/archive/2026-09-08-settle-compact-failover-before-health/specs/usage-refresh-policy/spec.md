## ADDED Requirements

### Requirement: Compact failover settles before account-health writes

When `compact_responses` holds an API-key usage reservation, it MUST NOT write account health for a compact upstream failure until that reservation has been settled or released. A `failover_next` decision MUST keep the same reservation for the next account and MUST defer the failed account's health write until the next settlement. Timeout and exhaustion terminals MUST keep settle-then-health order. Compact MUST NOT acquire a second reservation mid-request. If usage finalization fails but the fail-safe reservation release succeeds, compact MUST flush deferred health before surfacing `usage_settlement_failed`. If the reservation remains held because that fail-safe release also fails, deferred health MUST stay unapplied. After a compact reservation is finalized, a deferred health-persistence failure MUST NOT replace the successful compact response. If compact exits through cancellation or any exception other than a `ProxyResponseError` that already settled, it MUST settle or release the reservation and flush deferred health before propagating that exception. A later account-selection budget timeout after `failover_next` MUST use that same settle-and-flush path. Deferred health flush MUST complete even if the compact request is cancelled while that flush is awaiting a health write. If one deferred health write fails, compact MUST still attempt the remaining deferred health writes.

#### Scenario: Compact failover_next defers health until settle

- **GIVEN** a compact request with a held API-key reservation
- **AND** the first account fails with a `failover_next` class
- **WHEN** a later account completes and settlement runs
- **THEN** `_handle_stream_error` for the failed account runs only after that settlement
- **AND** the request does not acquire another reservation

#### Scenario: Compact timeout still settles before health

- **GIVEN** a compact request whose upstream call times out
- **WHEN** the timeout branch records account health
- **THEN** the reservation is settled before `_handle_stream_error`

#### Scenario: Compact HTTP 500 failover defers health until settle

- **GIVEN** a compact request with a held API-key reservation
- **AND** the first account exhausts same-account HTTP 500 retries
- **WHEN** a later account completes and settlement runs
- **THEN** `_handle_proxy_error` and extra `record_errors` for the failed account run only after that settlement

#### Scenario: Compact route failure after failover still applies deferred health

- **GIVEN** a compact request that deferred health on `failover_next`
- **WHEN** the next account raises `UpstreamProxyRouteError`
- **THEN** the reservation is settled
- **AND** the deferred health write still runs

#### Scenario: Compact refresh/connect failover defers health until settle

- **GIVEN** a compact request with a held API-key reservation
- **AND** the first account fails a retryable freshness/connect or post-401 forced-refresh transport error
- **WHEN** a later account completes and settlement runs
- **THEN** `_handle_stream_error` for the failed account runs only after that settlement

#### Scenario: Compact second 401 failover defers health until settle

- **GIVEN** a compact request with a held API-key reservation
- **AND** the same account returns 401 again after a forced refresh
- **WHEN** a later account completes and settlement runs
- **THEN** `_handle_proxy_error` for the failed account runs only after that settlement

#### Scenario: Compact permanent refresh settles before the health mark

- **GIVEN** a compact request with a held API-key reservation
- **AND** the post-401 forced refresh raises a permanent `RefreshError`
- **WHEN** the compact request records the permanent account failure
- **THEN** the reservation is settled before `mark_permanent_failure`

#### Scenario: Compact fallback release still flushes deferred health

- **GIVEN** a compact request that deferred health on `failover_next`
- **AND** a later account completes but usage finalization fails
- **AND** the fail-safe reservation release succeeds
- **WHEN** settlement surfaces `usage_settlement_failed`
- **THEN** the deferred health write still runs
- **AND** it runs before the `usage_settlement_failed` error is raised

#### Scenario: Compact unsettled reservation keeps deferred health unapplied

- **GIVEN** a compact request that deferred health on `failover_next`
- **AND** both usage finalization and fail-safe release fail
- **WHEN** settlement surfaces `usage_settlement_failed`
- **THEN** the deferred health write does not run

#### Scenario: Compact success survives deferred health persistence failure

- **GIVEN** a compact request that deferred health on `failover_next`
- **AND** a later account completes and usage finalization succeeds
- **WHEN** the deferred health write raises
- **THEN** the successful compact response is still returned

#### Scenario: Compact unexpected exit still flushes deferred health

- **GIVEN** a compact request that deferred health on `failover_next`
- **WHEN** the next account attempt raises cancellation or another non-proxy exception
- **THEN** the reservation is settled or released
- **AND** the deferred health write still runs
- **AND** the original exception is propagated

#### Scenario: Compact deferred health flush completes under cancellation

- **GIVEN** a compact request that deferred health on `failover_next`
- **AND** a later account completed and settlement started flushing
- **WHEN** the request is cancelled during the deferred health write
- **THEN** the deferred health write still completes

#### Scenario: Compact continues flushing after one deferred health write fails

- **GIVEN** a compact request that deferred health for more than one failed account
- **WHEN** the first deferred health write raises
- **THEN** later deferred health writes are still attempted

#### Scenario: Compact selection timeout after failover still flushes deferred health

- **GIVEN** a compact request that deferred health on `failover_next`
- **WHEN** selecting the next account exhausts the request budget
- **THEN** the reservation is settled or released
- **AND** the deferred health write still runs
- **AND** the original budget-timeout error is propagated
