# responses-api-compat Delta

## ADDED Requirements

### Requirement: Bridge-local previous-response recovery classifies normalized error frames

When the HTTP bridge evaluates whether a failed anchored request may enter bridge-local previous-response recovery, it MUST classify the error frame with the same normalization as the WebSocket rewrite path: a missing or empty `code` MUST fall back to the error `type` before classification, and the parameterless ``Invalid `previous_response_id`.`` invalid-request shape MUST classify as a previous-response continuity miss. A classifiable previous-response rejection MUST route into previous-response recovery and MUST NOT be treated as an ambiguous transport failure that only feeds the retry-circuit cooldown.

#### Scenario: Terse parameterless rejection enters local recovery

- **GIVEN** an anchored HTTP bridge request fails with `type = "invalid_request_error"`, no `code`, no `param`, and the message ``Invalid `previous_response_id`.``
- **WHEN** the bridge evaluates bridge-local previous-response recovery for that failure
- **THEN** the failure classifies as a previous-response continuity miss
- **AND** the bridge attempts previous-response recovery instead of the ambiguous-transport path

#### Scenario: Code carried only in the error type classifies

- **GIVEN** an anchored HTTP bridge request fails with no `code` and `type = "previous_response_not_found"`
- **WHEN** the bridge evaluates bridge-local previous-response recovery for that failure
- **THEN** the failure enters previous-response recovery instead of the ambiguous-transport class

#### Scenario: Unrelated errors keep their classification

- **WHEN** a failed anchored request carries an error whose normalized code, param, and message do not match a previous-response continuity miss
- **THEN** the bridge MUST NOT classify it as a previous-response continuity miss

## MODIFIED Requirements

### Requirement: Repeated zero-event idle failures poison dead anchors

For hard HTTP bridge keys, repeated zero-event failures MUST use the existing durable retry-circuit counter to identify an anchor that should no longer remain addressable; the counter resets on a completed response, so a run of consecutive failures proves the anchor never advanced. Both ambiguous eventless transport classes — `stream_idle_timeout` (including its aliased diagnostics) and `stream_incomplete` — MUST be able to trigger anchor poisoning at the threshold; a `clean_close` outcome MUST NOT itself trigger anchor poisoning. When an eligible eventless failure reaches the configured poison threshold for the same hard bridge key, the proxy MUST abandon durable continuity for that session and retire the bridge even when admission waiters exist, and the shared retirement boundary MUST clear the poisoned durable anchor even when no admission waiter exists, while the session still owns its durable lease. If the clear cannot be confirmed on the waiterless retirement path, the proxy MUST re-attempt it when a later eligible eventless failure at or above the threshold retires the session. The default threshold MUST be no greater than seven failures.

#### Scenario: Admission waiters cannot defer anchor poisoning forever

- **GIVEN** a hard durable bridge key has admission waiters
- **AND** repeated zero-event idle failures for that same key reach the poison
  threshold
- **WHEN** the reader failure path would normally defer retirement for the
  admission waiter
- **THEN** the proxy clears the durable continuity anchors
- **AND** retires the session despite the admission waiter
- **AND** the next attach starts from fresh durable state rather than the
  poisoned previous-response anchor

#### Scenario: Repeated eventless stream_incomplete failures poison the anchor

- **GIVEN** a hard durable bridge key has a stored durable anchor
- **AND** every anchored attempt fails eventlessly with `stream_incomplete` (for example a masked upstream previous-response rejection)
- **WHEN** consecutive failures for that key reach the poison threshold
- **THEN** the proxy clears the durable continuity anchors under the session's owner epoch
- **AND** the next attach starts from fresh durable state instead of looping through retry-circuit cooldown

#### Scenario: Waiterless retirement poisons the anchor at the threshold

- **GIVEN** a hard durable bridge key fails eventlessly with no admission waiters
- **WHEN** the shared retirement boundary records the eventless failure that reaches the poison threshold
- **THEN** the proxy clears the durable continuity anchors before releasing the durable lease

#### Scenario: Failed waiterless clear is re-attempted on the next threshold failure

- **GIVEN** the waiterless retirement path reached the poison threshold but the durable continuity clear could not be confirmed
- **WHEN** the next eligible eventless failure for the same key retires the session
- **THEN** the proxy re-attempts the durable continuity clear under the new session's owner epoch

#### Scenario: Clean closes never trigger anchor poisoning

- **WHEN** a `clean_close` retry-circuit outcome is recorded for a hard bridge key, at any consecutive-failure count
- **THEN** that outcome does not clear the durable continuity anchors

#### Scenario: Lease liveness comparison is timezone-safe
- **GIVEN** a durable bridge session whose `lease_expires_at` was read from a `timestamptz` column (offset-aware) on PostgreSQL
- **WHEN** the dead-owner classifier evaluates lease liveness against the application's naive-UTC clock
- **THEN** both timestamps MUST be normalized to naive UTC before comparison
- **AND** the anchored-lookup path MUST NOT raise on mixed-awareness datetimes
